import os
from sqlalchemy import create_engine, Column, Integer, String, Date, Float, ForeignKey
from sqlalchemy.orm import declarative_base, sessionmaker, relationship

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://user:password@localhost:5432/raceplanner")

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True)
    password = Column(String)
    name = Column(String)
    date_of_birth = Column(Date)
    gender = Column(String)
    height = Column(Float)
    weight = Column(Float)
    
    # Race details
    race_length = Column(String, nullable=True)
    race_date = Column(Date, nullable=True)
    race_goal_time = Column(String, nullable=True)
    
    # Strava auth
    strava_access_token = Column(String, nullable=True)
    strava_refresh_token = Column(String, nullable=True)
    strava_athlete_id = Column(Integer, nullable=True)

    races = relationship("Race", back_populates="user", cascade="all, delete-orphan")

class Race(Base):
    __tablename__ = "races"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True)
    name = Column(String, nullable=True) # E.g., 'Boston Marathon'
    race_length = Column(String)
    race_date = Column(Date)
    goal_time = Column(String, nullable=True)

    user = relationship("User", back_populates="races")

Base.metadata.create_all(bind=engine)
