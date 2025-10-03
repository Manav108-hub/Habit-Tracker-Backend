# auth_utils.py - Pure Authorization header based authentication
from datetime import datetime, timedelta
import time
import os
import bcrypt
from fastapi import Request, HTTPException, Depends
from jose import JWTError, jwt
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from dotenv import load_dotenv
from database import get_db
from model import User, AdminInvite

load_dotenv()

# Security settings
SECRET_KEY = os.getenv("SECRET_KEY", "mysecretkey")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30
ADMIN_CREATION_SECRET = os.getenv("ADMIN_CREATION_SECRET")

# Rate limiting for admin operations
admin_rate_limit = {}

def hash_password(password: str) -> str:
    """Hash password using bcrypt"""
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode('utf-8'), salt).decode('utf-8')

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify password against hash"""
    return bcrypt.checkpw(plain_password.encode('utf-8'), hashed_password.encode('utf-8'))

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
    """Get current user from Authorization header ONLY"""
    auth_header = request.headers.get("Authorization")
    
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=401, 
            detail="Authorization header with Bearer token required"
        )
    
    token = auth_header.replace("Bearer ", "").strip()
    
    if not token:
        raise HTTPException(status_code=401, detail="Token is empty")
    
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email: str = payload.get("sub")
        if email is None:
            raise HTTPException(status_code=401, detail="Invalid token payload")
        
        result = await db.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()
        if user is None:
            raise HTTPException(status_code=401, detail="User not found")
        
        return user
    except JWTError as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {str(e)}")

async def get_current_admin(request: Request, db: AsyncSession = Depends(get_db)):
    """Get current admin user"""
    user = await get_current_user(request, db)
    if user.role not in ["admin", "super_admin"]:
        raise HTTPException(status_code=403, detail="Admin access required")
    return user

async def get_current_super_admin(request: Request, db: AsyncSession = Depends(get_db)):
    """Get current super admin user"""
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