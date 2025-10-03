# schema.py - Complete schema definitions
from pydantic import BaseModel, Field, EmailStr
from typing import List, Optional, Dict, Any
from datetime import datetime

# User Schemas
class UserSignup(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8)

class UserLogin(BaseModel):
    email: EmailStr
    password: str

class UserCreateWithRole(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8)
    role: str = Field(default="user")

class UserResponse(BaseModel):
    id: int
    email: str
    role: str
    total_points: int = 0
    level: int = 1
    created_at: datetime
    
    class Config:
        from_attributes = True

# Admin Schemas
class CreateFirstAdminRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=12)
    admin_creation_secret: str

class AdminInviteRequest(BaseModel):
    email: EmailStr
    admin_creation_secret: str

class AdminInviteAccept(BaseModel):
    invite_token: str
    password: str = Field(..., min_length=12)

class AdminInviteResponse(BaseModel):
    email: str
    invite_token: str
    expires_at: datetime

# Habit Schemas
class HabitCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    category: Optional[str] = None
    difficulty_level: int = Field(default=1, ge=1, le=5)
    target_frequency: str = Field(default="daily")

class HabitCheckInResponse(BaseModel):
    id: int
    check_in_date: datetime
    points_earned: int = 0
    mood_rating: Optional[int] = None
    notes: Optional[str] = None
    
    class Config:
        from_attributes = True

class HabitResponse(BaseModel):
    id: int
    name: str
    description: Optional[str]
    category: Optional[str]
    difficulty_level: int
    start_date: datetime
    current_streak: int
    points_per_completion: int
    check_ins: List[HabitCheckInResponse] = []
    
    class Config:
        from_attributes = True

# Progress Schemas
class ProgressResponse(BaseModel):
    completedToday: int
    totalHabits: int
    completionRate: float = 0.0
    currentLevel: int = 1
    totalPoints: int = 0

# Badge Schemas
class BadgeResponse(BaseModel):
    id: int
    badge_type: str
    badge_name: str
    badge_description: str
    earned_at: datetime
    
    class Config:
        from_attributes = True

# AI Recommendation Schemas
class AIRecommendationResponse(BaseModel):
    id: int
    recommendation_type: str
    title: str
    content: str
    priority: int
    is_read: bool
    source_ai: Optional[str]
    created_at: datetime
    
    class Config:
        from_attributes = True

class RecommendationRequest(BaseModel):
    recommendation_type: str = Field(..., pattern="^(habit_suggestion|motivation|improvement)$")

# Stats Schemas
class UserStatsResponse(BaseModel):
    total_points: int
    level: int
    total_habits: int
    active_streaks: List[int]
    badges_count: int
    recent_badges: List[BadgeResponse]

# Check-in Request Schema
class CheckInRequest(BaseModel):
    mood_rating: Optional[int] = Field(None, ge=1, le=5)
    notes: Optional[str] = None

# Generic Response Schema
class MessageResponse(BaseModel):
    message: str

# Weekly Progress Schema
class DailyProgressResponse(BaseModel):
    date: str
    completed: int
    total: int
    completion_rate: float

class WeeklyProgressResponse(BaseModel):
    weekly_progress: List[DailyProgressResponse]

# Admin Analytics Schema
class AdminAnalyticsResponse(BaseModel):
    total_users: int
    total_habits: int
    total_checkins: int
    active_users_last_7_days: int
    average_habits_per_user: float