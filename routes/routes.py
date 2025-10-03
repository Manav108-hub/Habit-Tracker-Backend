# routes.py - Fixed version without auto check-in
from fastapi import APIRouter, HTTPException, Request, status, Depends, BackgroundTasks
from fastapi.responses import JSONResponse
from datetime import datetime, timedelta
from typing import List, Optional
import secrets
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func, desc, or_
from sqlalchemy.orm import selectinload

from auth_utils import (
    hash_password,
    verify_password,
    create_access_token,
    get_current_user,
    get_current_admin,
    get_current_super_admin,
    ACCESS_TOKEN_EXPIRE_MINUTES,
    verify_admin_creation_secret,
    rate_limit_admin_operations,
    verify_admin_invite_token,
    create_first_admin_if_none_exist
)
from database import get_db
from model import User, Habit, HabitCheckIn, UserBadge, AIRecommendation, AdminInvite
from schema import (
    UserSignup, 
    UserLogin, 
    UserCreateWithRole,
    HabitCreate,
    HabitResponse,
    UserResponse,
    MessageResponse,
    ProgressResponse,
    HabitCheckInResponse,
    BadgeResponse,
    AIRecommendationResponse,
    UserStatsResponse,
    RecommendationRequest,
    CreateFirstAdminRequest,
    AdminInviteRequest,
    AdminInviteAccept,
    AdminInviteResponse
)
from ai_service import AIRecommendationService
from gamification_service import GamificationService
import logging

router = APIRouter()
ai_service = AIRecommendationService()
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# -------------------------------
# Auth Routes
# -------------------------------

@router.post("/signup", response_model=MessageResponse)
async def signup(user: UserSignup, db: AsyncSession = Depends(get_db)):
    # Check if user already exists
    result = await db.execute(select(User).where(User.email == user.email))
    existing_user = result.scalar_one_or_none()
    
    if existing_user:
        raise HTTPException(status_code=400, detail="Email already registered")
    
    # Create new user
    hashed_pw = hash_password(user.password)
    new_user = User(
        email=user.email,
        hashed_password=hashed_pw
    )
    
    db.add(new_user)
    await db.commit()
    await db.refresh(new_user)
    
    return MessageResponse(message="User created successfully")

@router.post("/create-first-admin", response_model=MessageResponse)
async def create_first_admin(
    request: CreateFirstAdminRequest,
    req: Request,
    db: AsyncSession = Depends(get_db)
):
    """Create the first admin user - only works if no admins exist"""
    
    # Rate limiting
    client_ip = req.client.host
    if not rate_limit_admin_operations(client_ip, max_attempts=3):
        raise HTTPException(
            status_code=429,
            detail="Too many admin creation attempts. Try again later."
        )
    
    # Try to create first admin
    success = await create_first_admin_if_none_exist(
        request.email, 
        request.password, 
        request.admin_creation_secret, 
        db
    )
    
    if not success:
        raise HTTPException(
            status_code=400,
            detail="Invalid secret or admin already exists"
        )
    
    return MessageResponse(message="Super admin created successfully")

@router.post("/login", response_model=MessageResponse)
async def login(user: UserLogin, background_tasks: BackgroundTasks, db: AsyncSession = Depends(get_db)):
    # Get user from database
    result = await db.execute(select(User).where(User.email == user.email))
    db_user = result.scalar_one_or_none()

    if not db_user or not verify_password(user.password, db_user.hashed_password):
        logger.warning(f"Failed login attempt for email: {user.email}")
        raise HTTPException(status_code=401, detail="Invalid email or password")

    # Log successful login with role information
    logger.info(f"✅ LOGIN SUCCESS - User: {db_user.email} | Role: {db_user.role} | ID: {db_user.id}")
    
    # Special logging for admin logins
    if db_user.role in ["admin", "super_admin"]:
        logger.warning(f"🔐 ADMIN LOGIN - {db_user.role.upper()}: {db_user.email} | ID: {db_user.id}")
    
    token = create_access_token(
        data={"sub": user.email}, 
        expires_delta=timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )

    response = JSONResponse(content={
        "message": "Login successful",
        "user": {
            "email": db_user.email,
            "role": db_user.role,
            "is_admin": db_user.role in ["admin", "super_admin"]
        }
    })
    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        expires=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        samesite="lax",
        secure=False,
        domain="localhost",
        path="/"
    )
    
    # Schedule daily recommendations check (NOT auto check-ins)
    background_tasks.add_task(check_daily_recommendations, db_user.id, db)
    
    return response

@router.post("/logout", response_model=MessageResponse)
def logout():
    response = JSONResponse(content={"message": "Logged out"})
    response.delete_cookie("access_token")
    return response

# -------------------------------
# Admin Management Routes
# -------------------------------

@router.post("/admin/invite", response_model=AdminInviteResponse)
async def invite_admin(
    request: AdminInviteRequest,
    req: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_super_admin)
):
    """Super admin can invite new admins with additional security"""
    
    # Rate limiting
    client_ip = req.client.host
    if not rate_limit_admin_operations(client_ip):
        raise HTTPException(
            status_code=429, 
            detail="Too many admin operations. Try again later."
        )
    
    # Verify admin creation secret
    if not verify_admin_creation_secret(request.admin_creation_secret):
        raise HTTPException(status_code=403, detail="Invalid admin creation secret")
    
    # Check if user already exists
    result = await db.execute(select(User).where(User.email == request.email))
    existing_user = result.scalar_one_or_none()
    
    if existing_user:
        raise HTTPException(status_code=400, detail="User already exists")
    
    # Check for existing valid invite
    result = await db.execute(
        select(AdminInvite).where(
            AdminInvite.email == request.email,
            AdminInvite.is_used == False,
            AdminInvite.expires_at > datetime.utcnow()
        )
    )
    existing_invite = result.scalar_one_or_none()
    
    if existing_invite:
        raise HTTPException(status_code=400, detail="Valid invitation already exists")
    
    # Generate secure invite
    invite_token = secrets.token_urlsafe(32)
    expires_at = datetime.utcnow() + timedelta(hours=48)
    
    admin_invite = AdminInvite(
        email=request.email,
        invite_token=invite_token,
        invited_by=current_user.id,
        expires_at=expires_at
    )
    
    db.add(admin_invite)
    await db.commit()
    
    return AdminInviteResponse(
        email=request.email,
        invite_token=invite_token,
        expires_at=expires_at
    )

@router.post("/admin/accept-invite", response_model=MessageResponse)
async def accept_admin_invite(
    request: AdminInviteAccept,
    req: Request,
    db: AsyncSession = Depends(get_db)
):
    """Accept admin invitation and create admin account"""
    
    # Rate limiting
    client_ip = req.client.host
    if not rate_limit_admin_operations(client_ip):
        raise HTTPException(
            status_code=429,
            detail="Too many admin operations. Try again later."
        )
    
    # Verify invite token
    invite = await verify_admin_invite_token(request.invite_token, db)
    if not invite:
        raise HTTPException(status_code=400, detail="Invalid or expired invitation")
    
    # Check if user already exists
    result = await db.execute(select(User).where(User.email == invite.email))
    existing_user = result.scalar_one_or_none()
    
    if existing_user:
        raise HTTPException(status_code=400, detail="User already exists")
    
    # Create admin user
    hashed_password = hash_password(request.password)
    new_admin = User(
        email=invite.email,
        hashed_password=hashed_password,
        role="admin",
        is_active=True
    )
    
    # Mark invite as used
    invite.is_used = True
    invite.used_at = datetime.utcnow()
    
    db.add(new_admin)
    await db.commit()
    
    return MessageResponse(message=f"Admin account created successfully for {invite.email}")

@router.get("/admin/invites")
async def list_admin_invites(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_super_admin)
):
    """List all admin invitations (super admin only)"""
    
    result = await db.execute(
        select(AdminInvite)
        .order_by(AdminInvite.created_at.desc())
    )
    invites = result.scalars().all()
    
    return [{
        "id": invite.id,
        "email": invite.email,
        "is_used": invite.is_used,
        "expires_at": invite.expires_at,
        "created_at": invite.created_at,
        "used_at": invite.used_at
    } for invite in invites]

@router.delete("/admin/invites/{invite_id}")
async def revoke_admin_invite(
    invite_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_super_admin)
):
    """Revoke an admin invitation (super admin only)"""
    
    result = await db.execute(
        select(AdminInvite).where(AdminInvite.id == invite_id)
    )
    invite = result.scalar_one_or_none()
    
    if not invite:
        raise HTTPException(status_code=404, detail="Invitation not found")
    
    await db.delete(invite)
    await db.commit()
    
    return MessageResponse(message="Admin invitation revoked")

# -------------------------------
# Habit Routes
# -------------------------------

@router.post("/habits", response_model=HabitResponse)
async def create_habit(
    habit: HabitCreate, 
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    # Calculate points based on difficulty
    points_per_completion = habit.difficulty_level * 10
    
    new_habit = Habit(
        user_id=current_user.id,
        name=habit.name,
        description=habit.description,
        category=habit.category,
        difficulty_level=habit.difficulty_level,
        target_frequency=habit.target_frequency,
        points_per_completion=points_per_completion,
        start_date=datetime.utcnow()
    )
    
    db.add(new_habit)
    await db.commit()
    await db.refresh(new_habit)
    
    # Check for badges after creating habit
    await GamificationService.check_and_award_badges(current_user.id, db)
    
    # Load check-ins to calculate streak
    result = await db.execute(
        select(Habit)
        .options(selectinload(Habit.check_ins))
        .where(Habit.id == new_habit.id)
    )
    habit_with_checkins = result.scalar_one()
    
    return HabitResponse(
        id=habit_with_checkins.id,
        name=habit_with_checkins.name,
        description=habit_with_checkins.description,
        category=habit_with_checkins.category,
        difficulty_level=habit_with_checkins.difficulty_level,
        start_date=habit_with_checkins.start_date,
        current_streak=len(habit_with_checkins.check_ins),
        points_per_completion=habit_with_checkins.points_per_completion,
        check_ins=[HabitCheckInResponse(id=ci.id, check_in_date=ci.check_in_date, points_earned=ci.points_earned) 
                  for ci in habit_with_checkins.check_ins]
    )

@router.get("/habits", response_model=List[HabitResponse])
async def get_habits(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    result = await db.execute(
        select(Habit)
        .options(selectinload(Habit.check_ins))
        .where(Habit.user_id == current_user.id)
    )
    habits = result.scalars().all()
    
    habit_responses = []
    for habit in habits:
        # Calculate current streak
        streak = GamificationService._calculate_streak(habit.check_ins)
        
        habit_responses.append(HabitResponse(
            id=habit.id,
            name=habit.name,
            description=habit.description,
            category=habit.category,
            difficulty_level=habit.difficulty_level,
            start_date=habit.start_date,
            current_streak=streak,
            points_per_completion=habit.points_per_completion,
            check_ins=[HabitCheckInResponse(
                id=ci.id, 
                check_in_date=ci.check_in_date,
                points_earned=ci.points_earned,
                mood_rating=ci.mood_rating
            ) for ci in habit.check_ins]
        ))
    
    return habit_responses

@router.post("/check-in/{habit_id}", response_model=HabitResponse)
async def mark_habit_as_done(
    habit_id: int, 
    mood_rating: Optional[int] = None,
    notes: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    # Ensure user owns the habit
    result = await db.execute(
        select(Habit)
        .options(selectinload(Habit.check_ins))
        .where(and_(Habit.id == habit_id, Habit.user_id == current_user.id))
    )
    habit = result.scalar_one_or_none()
    
    if not habit:
        raise HTTPException(status_code=404, detail="Habit not found")

    today = datetime.utcnow().date()
    
    # Check if already checked in today
    already_checked_in = any(
        ci.check_in_date.date() == today for ci in habit.check_ins
    )
    
    if already_checked_in:
        raise HTTPException(
            status_code=400, 
            detail="Already checked in today for this habit"
        )
    
    # Calculate bonus points for streak
    current_streak = GamificationService._calculate_streak(habit.check_ins)
    streak_bonus = min(current_streak // 7, 5) * 5  # 5 bonus points per week in streak, max 25
    points_earned = habit.points_per_completion + streak_bonus
    
    new_checkin = HabitCheckIn(
        habit_id=habit.id,
        check_in_date=datetime.utcnow(),
        mood_rating=mood_rating,
        notes=notes,
        points_earned=points_earned
    )
    db.add(new_checkin)
    
    # Award points to user
    await GamificationService.award_points(current_user.id, points_earned, db)
    
    # Check for new badges
    new_badges = await GamificationService.check_and_award_badges(current_user.id, db)
    
    await db.commit()
    await db.refresh(new_checkin)
    await db.refresh(habit)

    # Calculate current streak for response
    streak = GamificationService._calculate_streak(habit.check_ins)

    # Return updated habit
    return HabitResponse(
        id=habit.id,
        name=habit.name,
        description=habit.description,
        category=habit.category,
        difficulty_level=habit.difficulty_level,
        start_date=habit.start_date,
        current_streak=streak,
        points_per_completion=habit.points_per_completion,
        check_ins=[HabitCheckInResponse(
            id=ci.id, 
            check_in_date=ci.check_in_date,
            points_earned=ci.points_earned,
            mood_rating=ci.mood_rating
        ) for ci in habit.check_ins]
    )

# -------------------------------
# Gamification Routes
# -------------------------------

@router.get("/stats", response_model=UserStatsResponse)
async def get_user_stats(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    # Get user badges
    badges_result = await db.execute(
        select(UserBadge)
        .where(UserBadge.user_id == current_user.id)
        .order_by(desc(UserBadge.earned_at))
    )
    badges = badges_result.scalars().all()
    
    # Get habits with streaks
    habits_result = await db.execute(
        select(Habit)
        .options(selectinload(Habit.check_ins))
        .where(and_(Habit.user_id == current_user.id, Habit.is_active == True))
    )
    habits = habits_result.scalars().all()
    
    active_streaks = []
    for habit in habits:
        streak = GamificationService._calculate_streak(habit.check_ins)
        active_streaks.append(streak)
    
    # Get recent badges (last 5)
    recent_badges = [
        BadgeResponse(
            id=badge.id,
            badge_type=badge.badge_type,
            badge_name=badge.badge_name,
            badge_description=badge.badge_description,
            earned_at=badge.earned_at
        ) for badge in badges[:5]
    ]
    
    return UserStatsResponse(
        total_points=current_user.total_points,
        level=current_user.level,
        total_habits=len(habits),
        active_streaks=active_streaks,
        badges_count=len(badges),
        recent_badges=recent_badges
    )

@router.get("/badges", response_model=List[BadgeResponse])
async def get_user_badges(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    result = await db.execute(
        select(UserBadge)
        .where(UserBadge.user_id == current_user.id)
        .order_by(desc(UserBadge.earned_at))
    )
    badges = result.scalars().all()
    
    return [
        BadgeResponse(
            id=badge.id,
            badge_type=badge.badge_type,
            badge_name=badge.badge_name,
            badge_description=badge.badge_description,
            earned_at=badge.earned_at
        ) for badge in badges
    ]

# -------------------------------
# AI Recommendation Routes
# -------------------------------

@router.post("/recommendations/generate", response_model=AIRecommendationResponse)
async def generate_recommendation(
    request: RecommendationRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Generate a new AI recommendation for the user"""
    recommendation = await ai_service.generate_recommendation(
        current_user.id, 
        request.recommendation_type, 
        db
    )
    
    if not recommendation:
        raise HTTPException(status_code=500, detail="Failed to generate recommendation")
    
    return AIRecommendationResponse(
        id=recommendation.id,
        recommendation_type=recommendation.recommendation_type,
        title=recommendation.title,
        content=recommendation.content,
        priority=recommendation.priority,
        is_read=recommendation.is_read,
        source_ai=recommendation.source_ai,
        created_at=recommendation.created_at
    )

@router.get("/recommendations", response_model=List[AIRecommendationResponse])
async def get_recommendations(
    limit: int = 10,
    unread_only: bool = False,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get user's AI recommendations"""
    query = select(AIRecommendation).where(AIRecommendation.user_id == current_user.id)
    
    if unread_only:
        query = query.where(AIRecommendation.is_read == False)
    
    # Only get non-expired recommendations
    query = query.where(
        or_(
            AIRecommendation.expires_at.is_(None),
            AIRecommendation.expires_at > datetime.utcnow()
        )
    )
    
    query = query.order_by(desc(AIRecommendation.created_at)).limit(limit)
    
    result = await db.execute(query)
    recommendations = result.scalars().all()
    
    return [
        AIRecommendationResponse(
            id=rec.id,
            recommendation_type=rec.recommendation_type,
            title=rec.title,
            content=rec.content,
            priority=rec.priority,
            is_read=rec.is_read,
            source_ai=rec.source_ai,
            created_at=rec.created_at
        ) for rec in recommendations
    ]

@router.patch("/recommendations/{recommendation_id}/read")
async def mark_recommendation_as_read(
    recommendation_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Mark a recommendation as read"""
    result = await db.execute(
        select(AIRecommendation)
        .where(and_(
            AIRecommendation.id == recommendation_id,
            AIRecommendation.user_id == current_user.id
        ))
    )
    recommendation = result.scalar_one_or_none()
    
    if not recommendation:
        raise HTTPException(status_code=404, detail="Recommendation not found")
    
    recommendation.is_read = True
    await db.commit()
    
    return {"message": "Recommendation marked as read"}

@router.get("/recommendations/daily")
async def get_daily_recommendations(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get today's personalized recommendations"""
    today = datetime.utcnow().date()
    
    # Check if we already have recommendations for today
    result = await db.execute(
        select(AIRecommendation)
        .where(and_(
            AIRecommendation.user_id == current_user.id,
            func.date(AIRecommendation.created_at) == today
        ))
    )
    existing_recommendations = result.scalars().all()
    
    if not existing_recommendations:
        # Generate daily recommendations
        motivation = await ai_service.generate_recommendation(
            current_user.id, "motivation", db
        )
        improvement = await ai_service.generate_recommendation(
            current_user.id, "improvement", db
        )
        
        recommendations = [r for r in [motivation, improvement] if r]
    else:
        recommendations = existing_recommendations
    
    return [
        AIRecommendationResponse(
            id=rec.id,
            recommendation_type=rec.recommendation_type,
            title=rec.title,
            content=rec.content,
            priority=rec.priority,
            is_read=rec.is_read,
            source_ai=rec.source_ai,
            created_at=rec.created_at
        ) for rec in recommendations
    ]

# -------------------------------
# Progress Routes
# -------------------------------

@router.get("/progress", response_model=ProgressResponse)
async def get_progress(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    today = datetime.utcnow().date()
    
    # Get total habits count
    total_habits_result = await db.execute(
        select(func.count(Habit.id)).where(and_(
            Habit.user_id == current_user.id,
            Habit.is_active == True
        ))
    )
    total_habits = total_habits_result.scalar()
    
    # Get habits completed today
    completed_today_result = await db.execute(
        select(func.count(func.distinct(Habit.id)))
        .select_from(Habit)
        .join(HabitCheckIn)
        .where(
            and_(
                Habit.user_id == current_user.id,
                Habit.is_active == True,
                func.date(HabitCheckIn.check_in_date) == today
            )
        )
    )
    completed_today = completed_today_result.scalar() or 0
    
    return ProgressResponse(
        completedToday=completed_today,
        totalHabits=total_habits,
        completionRate=(completed_today / total_habits * 100) if total_habits > 0 else 0,
        currentLevel=current_user.level,
        totalPoints=current_user.total_points
    )

@router.get("/progress/weekly")
async def get_weekly_progress(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get weekly progress statistics"""
    today = datetime.utcnow().date()
    week_start = today - timedelta(days=today.weekday())
    
    daily_progress = []
    for i in range(7):
        day = week_start + timedelta(days=i)
        
        # Get total habits for this day
        total_habits_result = await db.execute(
            select(func.count(Habit.id)).where(and_(
                Habit.user_id == current_user.id,
                Habit.is_active == True,
                func.date(Habit.start_date) <= day
            ))
        )
        total_habits = total_habits_result.scalar()
        
        # Get completed habits for this day
        completed_result = await db.execute(
            select(func.count(func.distinct(Habit.id)))
            .select_from(Habit)
            .join(HabitCheckIn)
            .where(and_(
                Habit.user_id == current_user.id,
                Habit.is_active == True,
                func.date(HabitCheckIn.check_in_date) == day
            ))
        )
        completed = completed_result.scalar() or 0
        
        daily_progress.append({
            "date": day.isoformat(),
            "completed": completed,
            "total": total_habits,
            "completion_rate": (completed / total_habits * 100) if total_habits > 0 else 0
        })
    
    return {"weekly_progress": daily_progress}

# -------------------------------
# Admin Routes
# -------------------------------

@router.get("/admin/users", response_model=List[UserResponse])
async def get_users(db: AsyncSession = Depends(get_db), _: User = Depends(get_current_admin)):
    result = await db.execute(select(User))
    users = result.scalars().all()
    return users

@router.post("/admin/users", response_model=MessageResponse)
async def create_user(
    user: UserCreateWithRole, 
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_admin)
):
    # Check if user already exists
    result = await db.execute(select(User).where(User.email == user.email))
    existing_user = result.scalar_one_or_none()
    
    if existing_user:
        raise HTTPException(status_code=400, detail="Email already registered")

    # Create new user
    hashed_pw = hash_password(user.password)
    new_user = User(
        email=user.email,
        hashed_password=hashed_pw,
        role=user.role
    )
    
    db.add(new_user)
    await db.commit()
    await db.refresh(new_user)
    
    return MessageResponse(message="User created successfully")

@router.get("/admin/analytics")
async def get_admin_analytics(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_admin)
):
    """Get platform-wide analytics for admins"""
    
    # Total users
    total_users_result = await db.execute(select(func.count(User.id)))
    total_users = total_users_result.scalar()
    
    # Total habits
    total_habits_result = await db.execute(select(func.count(Habit.id)))
    total_habits = total_habits_result.scalar()
    
    # Total check-ins
    total_checkins_result = await db.execute(select(func.count(HabitCheckIn.id)))
    total_checkins = total_checkins_result.scalar()
    
    # Active users (users with check-ins in last 7 days)
    week_ago = datetime.utcnow() - timedelta(days=7)
    active_users_result = await db.execute(
        select(func.count(func.distinct(Habit.user_id)))
        .select_from(Habit)
        .join(HabitCheckIn)
        .where(HabitCheckIn.check_in_date >= week_ago)
    )
    active_users = active_users_result.scalar()
    
    return {
        "total_users": total_users,
        "total_habits": total_habits,
        "total_checkins": total_checkins,
        "active_users_last_7_days": active_users,
        "average_habits_per_user": total_habits / total_users if total_users > 0 else 0
    }

# -------------------------------
# User Profile Route
# -------------------------------

@router.get("/me", response_model=UserResponse)
async def get_me(current_user: User = Depends(get_current_user)):
    return UserResponse(
        id=current_user.id,
        email=current_user.email,
        role=current_user.role,
        total_points=current_user.total_points,
        level=current_user.level,
        created_at=current_user.created_at
    )

# -------------------------------
# Background Tasks
# -------------------------------

async def check_daily_recommendations(user_id: int, db: AsyncSession):
    """Background task to generate daily recommendations if needed"""
    today = datetime.utcnow().date()
    
    # Check if user already has recommendations for today
    result = await db.execute(
        select(AIRecommendation)
        .where(and_(
            AIRecommendation.user_id == user_id,
            func.date(AIRecommendation.created_at) == today
        ))
    )
    existing = result.scalars().first()
    
    if not existing:
        # Generate motivation recommendation
        await ai_service.generate_recommendation(user_id, "motivation", db)