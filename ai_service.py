# ai_service.py
import asyncio
from typing import Dict, Any, Optional
from datetime import datetime, timedelta
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
import os
from google import genai
from model import User, Habit, HabitCheckIn, AIRecommendation

class AIRecommendationService:
    def __init__(self):
        self.gemini_api_key = os.getenv("GEMINI_API_KEY")
        self.grok_api_key = os.getenv("GROK_API_KEY")
        
    async def get_user_analytics(self, user_id: int, db: AsyncSession) -> Dict[str, Any]:
        """Get comprehensive user analytics for AI recommendations"""
        result = await db.execute(
            select(Habit)
            .where(and_(Habit.user_id == user_id, Habit.is_active == True))
        )
        habits = result.scalars().all()
        
        total_habits = len(habits)
        active_streaks = []
        completion_rates = []
        categories = {}
        
        for habit in habits:
            thirty_days_ago = datetime.utcnow() - timedelta(days=30)
            checkins_result = await db.execute(
                select(HabitCheckIn)
                .where(and_(
                    HabitCheckIn.habit_id == habit.id,
                    HabitCheckIn.check_in_date >= thirty_days_ago
                ))
            )
            recent_checkins = checkins_result.scalars().all()
            
            streak = self._calculate_current_streak(recent_checkins)
            active_streaks.append(streak)
            
            days_since_creation = (datetime.utcnow() - habit.start_date).days + 1
            completion_rate = len(recent_checkins) / min(30, days_since_creation) * 100
            completion_rates.append(completion_rate)
            
            category = habit.category or "general"
            categories[category] = categories.get(category, 0) + 1
        
        return {
            "total_habits": total_habits,
            "average_streak": sum(active_streaks) / len(active_streaks) if active_streaks else 0,
            "average_completion_rate": sum(completion_rates) / len(completion_rates) if completion_rates else 0,
            "best_streak": max(active_streaks) if active_streaks else 0,
            "categories": categories,
            "struggling_habits": [i for i, rate in enumerate(completion_rates) if rate < 50],
            "strong_habits": [i for i, rate in enumerate(completion_rates) if rate > 80]
        }
    
    def _calculate_current_streak(self, checkins: list[HabitCheckIn]) -> int:
        """Calculate current streak for a habit"""
        if not checkins:
            return 0
        
        sorted_checkins = sorted(checkins, key=lambda x: x.check_in_date, reverse=True)
        streak = 0
        current_date = datetime.utcnow().date()
        
        for checkin in sorted_checkins:
            checkin_date = checkin.check_in_date.date()
            expected_date = current_date - timedelta(days=streak)
            
            if checkin_date == expected_date:
                streak += 1
            else:
                break
        
        return streak
    
    async def get_gemini_recommendation(self, prompt: str) -> Optional[str]:
        """Get recommendation from Gemini AI using new SDK"""
        try:
            client = genai.Client(api_key=self.gemini_api_key)
            
            response = client.models.generate_content(
                model="gemini-2.0-flash-exp",
                contents=prompt
            )
            
            return response.text
        except Exception as e:
            print(f"Gemini API error: {e}")
            return None
    
    def _create_recommendation_prompt(self, user_analytics: Dict[str, Any], recommendation_type: str) -> str:
        """Create a prompt for AI recommendation based on user analytics"""
        base_context = f"""
        User Habit Analytics:
        - Total active habits: {user_analytics['total_habits']}
        - Average streak: {user_analytics['average_streak']:.1f} days
        - Average completion rate: {user_analytics['average_completion_rate']:.1f}%
        - Best streak: {user_analytics['best_streak']} days
        - Habit categories: {user_analytics['categories']}
        - Number of struggling habits: {len(user_analytics['struggling_habits'])}
        - Number of strong habits: {len(user_analytics['strong_habits'])}
        """
        
        if recommendation_type == "habit_suggestion":
            return f"""
            {base_context}
            
            Based on this user's habit tracking data, suggest 1-2 new habits they could add to improve their life. 
            Consider their current categories and completion rates. Provide specific, actionable habit suggestions.
            Keep the response concise (max 200 words) and motivational.
            """
        
        elif recommendation_type == "motivation":
            return f"""
            {base_context}
            
            Create a motivational message for this user based on their habit tracking performance. 
            Acknowledge their progress and encourage them to keep going. If they're struggling, 
            provide gentle encouragement and practical tips. Keep it personal and under 150 words.
            """
        
        elif recommendation_type == "improvement":
            return f"""
            {base_context}
            
            Analyze the user's habit data and provide 1-2 specific suggestions for improving their 
            habit tracking success. Focus on practical strategies they can implement immediately.
            Keep the response actionable and under 200 words.
            """
        
        return base_context
    
    def _get_fallback_recommendation(self, analytics: Dict[str, Any], rec_type: str) -> str:
        """Provide fallback recommendations when AI APIs are unavailable"""
        if rec_type == "motivation":
            if analytics['average_completion_rate'] > 70:
                return f"Excellent work! You're maintaining a {analytics['average_completion_rate']:.0f}% completion rate across {analytics['total_habits']} habits. Your consistency is building strong foundations for lasting change. Keep up the momentum!"
            else:
                return f"You've taken the first step by tracking {analytics['total_habits']} habits. Remember, building habits takes time. Focus on completing one habit at a time, and celebrate small wins. Progress, not perfection!"
        
        elif rec_type == "improvement":
            if len(analytics['struggling_habits']) > 0:
                return f"Consider focusing on your top performing habits first. You have {len(analytics['strong_habits'])} habits with high completion rates. Build on these successes before adding new ones. Try setting reminders or habit stacking to improve struggling habits."
            else:
                return f"Great foundation! To level up, try the '2-minute rule' - make habits so easy they take less than 2 minutes to start. This reduces resistance and builds momentum. Also, track your 'why' - write down the reason behind each habit."
        
        elif rec_type == "habit_suggestion":
            top_category = max(analytics['categories'].items(), key=lambda x: x[1])[0] if analytics['categories'] else 'general'
            return f"Based on your focus on {top_category}, consider adding: 1) A reflection habit - spend 5 minutes journaling about what's working. 2) A preparation habit - plan tomorrow's tasks each evening. Small habits that support your existing routines compound results!"
        
        return "Keep tracking your habits consistently. Small daily actions lead to remarkable long-term results!"
    
    async def generate_recommendation(self, user_id: int, recommendation_type: str, db: AsyncSession) -> Optional[AIRecommendation]:
        """Generate a recommendation using Gemini AI with fallback"""
        user_analytics = await self.get_user_analytics(user_id, db)
        prompt = self._create_recommendation_prompt(user_analytics, recommendation_type)
        
        # Try Gemini API
        gemini_response = await self.get_gemini_recommendation(prompt)
        
        selected_response = None
        source_ai = None
        
        if gemini_response:
            selected_response = gemini_response
            source_ai = "gemini"
        else:
            # Fallback to system-generated recommendations
            selected_response = self._get_fallback_recommendation(user_analytics, recommendation_type)
            source_ai = "system"
        
        if selected_response:
            recommendation = AIRecommendation(
                user_id=user_id,
                recommendation_type=recommendation_type,
                title=f"{recommendation_type.replace('_', ' ').title()}",
                content=selected_response,
                source_ai=source_ai,
                priority=1,
                expires_at=datetime.utcnow() + timedelta(days=7)
            )
            
            db.add(recommendation)
            await db.commit()
            await db.refresh(recommendation)
            
            return recommendation
        
        return None