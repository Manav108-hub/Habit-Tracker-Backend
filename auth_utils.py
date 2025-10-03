# auth_utils.py - Fixed version without auto check-in functionality
from datetime import datetime, timedelta
import time
import secrets
import os
from fastapi import Request, HTTPException, status, Depends
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, delete
from sqlalchemy.orm import selectinload
from dotenv import load_dotenv
from database import get_db
from model import User, Habit, HabitCheckIn, AdminInvite

load_dotenv()

# Security settings
SECRET_KEY = os.getenv("SECRET_KEY", "mysecretkey")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30
ADMIN_CREATION_SECRET = os.getenv("ADMIN_CREATION_SECRET")

# Rate limiting for admin operations
admin_rate_limit = {}

# Password hashing setup
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def hash_password(password: str) -> str:
    return pwd_context.hash(password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

def create_access_token(data: dict, expires_delta: timedelta = None):
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=15))
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

def verify_admin_creation_secret(secret: str) -> bool:
    """Verify the admin creation secret"""
    if not ADMIN_CREATION_SECRET:
        return False
    return secret == ADMIN_CREATION_SECRET

def rate_limit_admin_operations(ip_address: str, max_attempts: int = 5, window_minutes: int = 15):
    """Rate limit admin operations by IP"""
    current_time = time.time()
    window_start = current_time - (window_minutes * 60)
    
    if ip_address not in admin_rate_limit:
        admin_rate_limit[ip_address] = []
    
    # Clean old attempts
    admin_rate_limit[ip_address] = [
        attempt_time for attempt_time in admin_rate_limit[ip_address]
        if attempt_time > window_start
    ]
    
    # Check if rate limit exceeded
    if len(admin_rate_limit[ip_address]) >= max_attempts:
        return False
    
    # Record this attempt
    admin_rate_limit[ip_address].append(current_time)
    return True

async def create_first_admin_if_none_exist(email: str, password: str, secret: str, db: AsyncSession) -> bool:
    """Create first admin if no admins exist and secret is correct"""
    # Verify secret
    if not verify_admin_creation_secret(secret):
        return False
    
    # Check if any admin exists
    result = await db.execute(
        select(User).where(User.role.in_(["admin", "super_admin"]))
    )
    existing_admin = result.scalar_one_or_none()
    
    if existing_admin:
        return False  # Admin already exists
    
    # Check if user email already exists
    result = await db.execute(select(User).where(User.email == email))
    existing_user = result.scalar_one_or_none()
    
    if existing_user:
        return False  # User already exists
    
    # Create super admin
    hashed_password = hash_password(password)
    super_admin = User(
        email=email,
        hashed_password=hashed_password,
        role="super_admin",
        is_active=True
    )
    
    db.add(super_admin)
    await db.commit()
    return True

async def get_current_user(request: Request, db: AsyncSession = Depends(get_db)):
    token = request.cookies.get("access_token")
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email: str = payload.get("sub")
        if email is None:
            raise HTTPException(status_code=401, detail="Invalid credentials")
        
        # Get user from database
        result = await db.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()
        if user is None:
            raise HTTPException(status_code=401, detail="User not found")
        
        return user
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid credentials")

async def get_current_admin(request: Request, db: AsyncSession = Depends(get_db)):
    user = await get_current_user(request, db)
    if user.role not in ["admin", "super_admin"]:
        raise HTTPException(status_code=403, detail="Not authorized as admin")
    return user

async def get_current_super_admin(request: Request, db: AsyncSession = Depends(get_db)):
    user = await get_current_user(request, db)
    if user.role != "super_admin":
        raise HTTPException(status_code=403, detail="Super admin access required")
    return user

async def verify_admin_invite_token(token: str, db: AsyncSession):
    """Verify admin invitation token"""
    result = await db.execute(
        select(AdminInvite).where(
            AdminInvite.invite_token == token,
            AdminInvite.is_used == False,
            AdminInvite.expires_at > datetime.utcnow()
        )
    )
    return result.scalar_one_or_none()


# ✅ REMOVED: update_user_habit_checkins function
# This function was causing automatic check-ins on every login
# Check-ins should ONLY be created when users explicitly click "Check In" button
# If you need streak analysis, create a separate read-only analytics function