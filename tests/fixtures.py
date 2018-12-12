import json
import os
import random

import pytest
import psycopg2
import arrow
from faker import Faker
from chance import chance

from target_postgres.singer_stream import SINGER_SEQUENCE

CONFIG = {
    'postgres_host': os.environ['POSTGRES_HOST'],
    'postgres_database': os.environ['POSTGRES_DATABASE'],
    'postgres_username': os.environ['POSTGRES_USERNAME']
}

TEST_DB = {
    'host': CONFIG['postgres_host'],
    'dbname': CONFIG['postgres_database'],
    'user': CONFIG['postgres_username']
}

fake = Faker()

CATS_SCHEMA = {
    'type': 'SCHEMA',
    'stream': 'cats',
    'schema': {
        'additionalProperties': False,
        'properties': {
            'id': {
                'type': 'integer'
            },
            'name': {
                'type': ['string']
            },
            'paw_size': {
                'type': ['integer'],
                'default': 314159
            },
            'paw_colour': {
                'type': 'string',
                'default': ''
            },
            'flea_check_complete': {
                'type': ['boolean'],
                'default': False
            },
            'pattern': {
                'type': ['null', 'string']
            },
            'age': {
                'type': ['null', 'integer']
            },
            'adoption': {
                'type': ['object', 'null'],
                'properties': {
                    'adopted_on': {
                        'type': ['null','string'],
                        'format': 'date-time'
                    },
                    'was_foster': {
                        'type': 'boolean'
                    },
                    'immunizations': {
                        'type': ['null','array'],
                        'items': {
                            'type': ['object'],
                            'properties': {
                                'type': {
                                    'type': ['null','string']
                                },
                                'date_administered': {
                                    'type': ['null','string'],
                                    'format': 'date-time'
                                }
                            }
                        }
                    }
                }
            }
        }
    },
    'key_properties': ['id']
}

class FakeStream(object):
    def __init__(self,
                 n,
                 *args,
                 version=None,
                 nested_count=0,
                 duplicates=0,
                 duplicate_sequence_delta=200,
                 sequence=None,
                 **kwargs):
        self.n = n
        self.wrote_schema = False
        self.id = 1
        self.nested_count = nested_count
        self.version = version
        self.wrote_activate_version = False
        self.records = []
        self.duplicates = duplicates
        self.duplicates_written = 0
        self.duplicate_pks_used = []
        self.record_message_count = 0
        if sequence:
            self.sequence = sequence
        else:
            self.sequence = arrow.get().timestamp
        self.duplicate_sequence_delta = duplicate_sequence_delta

    def duplicate(self, force=False):
        if self.duplicates > 0 and \
           len(self.records) > 0 and \
           self.duplicates_written < self.duplicates and \
           (force or chance.boolean(likelihood=30)):
            self.duplicates_written += 1
            random_index = random.randint(0, len(self.records) - 1)
            record = self.records[random_index]
            self.duplicate_pks_used.append(record['id'])
            record_message = self.generate_record_message(record=record)
            record_message['sequence'] = self.sequence + self.duplicate_sequence_delta
            return record_message
        else:
            return False

    def generate_record_message(self, record=None):
        if not record:
            record = self.generate_record()
            self.id += 1

        self.records.append(record)
        message = {
            'type': 'RECORD',
            'stream': self.stream,
            'record': record,
            'sequence': self.sequence
        }

        if self.version is not None:
            message['version'] = self.version

        self.record_message_count += 1

        return message

    def activate_version(self):
        self.wrote_activate_version = True
        return {
            'type': 'ACTIVATE_VERSION',
            'stream': self.stream,
            'version': self.version
        }

    def __iter__(self):
        return self

    def __next__(self):
        if not self.wrote_schema:
            self.wrote_schema = True
            return json.dumps(self.schema)
        if self.id <= self.n:
            dup = self.duplicate()
            if dup != False:
                return json.dumps(dup)
            return json.dumps(self.generate_record_message())
        if self.id == self.n:
            dup = self.duplicate(force=True)
            if dup != False:
                return json.dumps(dup)
        if self.version is not None and self.wrote_activate_version == False:
            return json.dumps(self.activate_version())
        raise StopIteration

class CatStream(FakeStream):
    stream = 'cats'
    schema = CATS_SCHEMA

    def generate_record(self):
        adoption = None
        if self.nested_count or chance.boolean(likelihood=70):
            immunizations = []
            for i in range(0, self.nested_count or random.randint(0, 4)):
                immunizations.append({
                    'type': chance.pickone(['FIV', 'Panleukopenia', 'Rabies', 'Feline Leukemia']),
                    'date_administered': chance.date(minyear=2012).isoformat()
                })
            adoption = {
                'adopted_on': chance.date(minyear=2012).isoformat(),
                'was_foster': chance.boolean(),
                'immunizations': immunizations
            }

        return {
            'id': self.id,
            'name': fake.first_name(),
            'pattern': chance.pickone(['Tabby', 'Tuxedo', 'Calico', 'Tortoiseshell']),
            'age': random.randint(1, 15),
            'adoption': adoption
        }

class InvalidCatStream(CatStream):
    def generate_record(self):
        record = CatStream.generate_record(self)

        if chance.boolean(likelihood=50):
            record['adoption'] = ['invalid', 'adoption']
        elif chance.boolean(likelihood=50):
            record['age'] = 'very invalid age'
        elif record['adoption'] and chance.boolean(likelihood=50):
            record['adoption']['immunizations'] = {
                'type': chance.pickone(['a', 'b', 'c']),
                'date_administered': ['clearly', 'not', 'a', 'date']
            }
        else:
            record['name'] = 22/7

        return record


def clear_db():
    with psycopg2.connect(**TEST_DB) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
            drop_command = ''
            for table in cur.fetchall():
                drop_command += 'DROP TABLE IF EXISTS ' + table[0] + ';'
            cur.execute('begin;' +
                        drop_command +
                        'commit;')

@pytest.fixture
def db_cleanup():
    clear_db()

    yield

    clear_db()
