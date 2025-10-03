from sqlalchemy import Column, Integer, String, DateTime, Text, ForeignKey, Boolean, Float, JSON
from sqlalchemy.orm import declarative_base, relationship
from datetime import datetime
from enum import Enum

Base = declarative_base()

class BadgeType(str, Enum):
    STREAK_STARTER = "streak_starter"  # 3 day streak
    WEEK_WARRIOR = "week_warrior"     # 7 day streak
    MONTH_MASTER = "month_master"     # 30 day streak
    HABIT_CREATOR = "habit_creator"   # Create 5 habits
    CONSISTENCY_KING = "consistency_king"  # 90% completion rate
    EARLY_BIRD = "early_bird"         # Check-in before 8 AM
    NIGHT_OWL = "night_owl"          # Check-in after 10 PM

class AdminInvite(Base):
    __tablename__ = "admin_invites"
    
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), nullable=False, index=True)
    invite_token = Column(String(255), unique=True, nullable=False, index=True)
    invited_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    expires_at = Column(DateTime, nullable=False)
    is_used = Column(Boolean, default=False, nullable=False)
    used_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    # Relationship
    inviter = relationship("User", foreign_keys=[invited_by])

class User(Base):
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, index=True, nullable=False)
    hashed_password = Column(String(255), nullable=False)
    role = Column(String(50), default="user", nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    total_points = Column(Integer, default=0, nullable=False)
    level = Column(Integer, default=1, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    # Relationships
    habits = relationship("Habit", back_populates="user", cascade="all, delete-orphan")
    badges = relationship("UserBadge", back_populates="user", cascade="all, delete-orphan")
    ai_recommendations = relationship("AIRecommendation", back_populates="user", cascade="all, delete-orphan")

class Habit(Base):
    __tablename__ = "habits"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    description = Column(Text)
    category = Column(String(100))  # health, productivity, learning, etc.
    difficulty_level = Column(Integer, default=1)  # 1-5 scale
    target_frequency = Column(String(50), default="daily")  # daily, weekly, monthly
    start_date = Column(DateTime, default=datetime.utcnow, nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    points_per_completion = Column(Integer, default=10, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    # Relationships
    user = relationship("User", back_populates="habits")
    check_ins = relationship("HabitCheckIn", back_populates="habit", cascade="all, delete-orphan")

class HabitCheckIn(Base):
    __tablename__ = "habit_check_ins"
    
    id = Column(Integer, primary_key=True, index=True)
    habit_id = Column(Integer, ForeignKey("habits.id"), nullable=False)
    check_in_date = Column(DateTime, default=datetime.utcnow, nullable=False)
    notes = Column(Text)
    mood_rating = Column(Integer)  # 1-5 scale for tracking mood
    points_earned = Column(Integer, default=10, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    # Relationship
    habit = relationship("Habit", back_populates="check_ins")

class UserBadge(Base):
    __tablename__ = "user_badges"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    badge_type = Column(String(50), nullable=False)
    badge_name = Column(String(255), nullable=False)
    badge_description = Column(Text)
    earned_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    # Relationship
    user = relationship("User", back_populates="badges")

class AIRecommendation(Base):
    __tablename__ = "ai_recommendations"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    recommendation_type = Column(String(50), nullable=False)
    title = Column(String(255), nullable=False)
    content = Column(Text, nullable=False)
    priority = Column(Integer, default=1)
    is_read = Column(Boolean, default=False)
    source_ai = Column(String(50))
    extra_data = Column(JSON)  # Changed from 'metadata' to 'extra_data'
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    expires_at = Column(DateTime)
    
    # Relationship
    user = relationship("User", back_populates="ai_recommendations")