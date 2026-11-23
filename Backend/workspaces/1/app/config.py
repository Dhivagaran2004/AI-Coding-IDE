import os

basedir = os.path.abspath(os.path.dirname(__file__))

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'dev-secret-key-change-in-production'
    # Default PostgreSQL connection: postgresql://username:password@host:port/database
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URI') or \
        'postgresql://postgres:@108.181.226.160:5434/source_orders'
    SQLALCHEMY_TRACK_MODIFICATIONS = False
