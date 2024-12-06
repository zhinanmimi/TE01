from sqlalchemy import create_engine, Column, Integer, String, Boolean, DateTime, ForeignKey, Interval # type: ignore
from sqlalchemy.ext.declarative import declarative_base # type: ignore
from sqlalchemy.orm import sessionmaker, relationship # type: ignore
from config import DATABASE_URL

Base = declarative_base()
engine = create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)

class User(Base):
    __tablename__ = 'users'
    
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, unique=True)
    can_schedule = Column(Boolean, default=False)
    can_delete = Column(Boolean, default=False)
    can_manage_groups = Column(Boolean, default=False)
    can_monitor = Column(Boolean, default=False)
    can_manage_whitelist = Column(Boolean, default=False)

class Group(Base):
    __tablename__ = 'groups'
    
    id = Column(Integer, primary_key=True)
    group_id = Column(Integer, unique=True)
    group_name = Column(String)
    monitoring_enabled = Column(Boolean, default=True)

class ScheduledMessage(Base):
    __tablename__ = 'scheduled_messages'
    
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer)
    group_id = Column(Integer)
    message_text = Column(String)
    message_type = Column(String, default='text')  # 'text', 'photo', 'video'
    schedule_time = Column(DateTime)
    delete_time = Column(DateTime, nullable=True)
    is_recurring = Column(Boolean, default=False)
    interval = Column(Interval, nullable=True)
    message_id = Column(Integer, nullable=True)

Base.metadata.create_all(engine) 