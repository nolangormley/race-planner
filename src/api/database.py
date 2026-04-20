import os
from sqlalchemy import create_engine, Column, Integer, String, Date, Float
from sqlalchemy.orm import declarative_base, sessionmaker

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

Base.metadata.create_all(bind=engine)
