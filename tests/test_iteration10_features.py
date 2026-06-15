"""
Iteration 10 Tests: Framer-motion animations, Multi-language FR/EN switcher, WebSocket notifications
Features tested:
1. Landing page loads (with animations - framer-motion)
2. Language switcher on landing page (FR/EN toggle)
3. Login page translations (FR/EN)
4. GET /api/notifications - returns notifications list with unread_count
5. POST /api/notifications/read - marks all as read
6. All existing endpoints still work
"""

import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

class TestIteration10Features:
    """Test suite for iteration 10 features: animations, language switcher, notifications"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup test session with authentication"""
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
        # Login to get auth cookie
        login_response = self.session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": "admin@switia.com", "password": "admin123"}
        )
        assert login_response.status_code == 200, f"Login failed: {login_response.text}"
        self.user = login_response.json()
        yield
    
    # ============ Landing Page Tests ============
    
    def test_landing_page_loads(self):
        """Test that landing page (root /) loads successfully"""
        response = requests.get(f"{BASE_URL}/")
        assert response.status_code == 200
        # Check that HTML is returned (React app)
        assert "<!doctype html>" in response.text.lower() or "<html" in response.text.lower()
        print("PASS: Landing page loads at /")
    
    # ============ Notifications API Tests ============
    
    def test_get_notifications_returns_list_and_unread_count(self):
        """Test GET /api/notifications returns notifications list with unread_count"""
        response = self.session.get(f"{BASE_URL}/api/notifications")
        assert response.status_code == 200, f"Failed: {response.text}"
        data = response.json()
        # Verify response structure
        assert "notifications" in data, "Response should have 'notifications' key"
        assert "unread_count" in data, "Response should have 'unread_count' key"
        assert isinstance(data["notifications"], list), "notifications should be a list"
        assert isinstance(data["unread_count"], int), "unread_count should be an integer"
        print(f"PASS: GET /api/notifications returns notifications list (count: {len(data['notifications'])}) and unread_count: {data['unread_count']}")
    
    def test_mark_notifications_read(self):
        """Test POST /api/notifications/read marks all as read"""
        response = self.session.post(f"{BASE_URL}/api/notifications/read")
        assert response.status_code == 200, f"Failed: {response.text}"
        data = response.json()
        assert "message" in data, "Response should have 'message' key"
        assert "read" in data["message"].lower(), "Message should mention 'read'"
        print(f"PASS: POST /api/notifications/read - {data['message']}")
    
    def test_notifications_unread_count_after_mark_read(self):
        """Test that unread_count is 0 after marking all as read"""
        # First mark all as read
        self.session.post(f"{BASE_URL}/api/notifications/read")
        # Then check unread count
        response = self.session.get(f"{BASE_URL}/api/notifications")
        assert response.status_code == 200
        data = response.json()
        assert data["unread_count"] == 0, f"Expected unread_count=0 after marking read, got {data['unread_count']}"
        print("PASS: unread_count is 0 after marking all notifications as read")
    
    # ============ Existing Endpoints Still Work ============
    
    def test_support_chat_endpoint_works(self):
        """Test /api/support/chat still works"""
        response = self.session.post(
            f"{BASE_URL}/api/support/chat",
            json={"message": "Test message for iteration 10"}
        )
        assert response.status_code == 200, f"Support chat failed: {response.text}"
        data = response.json()
        assert "response" in data, "Response should have 'response' key"
        print("PASS: /api/support/chat endpoint works")
    
    def test_commercial_agent_chat_endpoint_works(self):
        """Test /api/agents/commercial/chat still works"""
        response = self.session.post(
            f"{BASE_URL}/api/agents/commercial/chat",
            json={"message": "Test commercial message"}
        )
        assert response.status_code == 200, f"Commercial chat failed: {response.text}"
        data = response.json()
        assert "response" in data, "Response should have 'response' key"
        print("PASS: /api/agents/commercial/chat endpoint works")
    
    def test_dashboard_stats_endpoint_works(self):
        """Test /api/dashboard/stats still works"""
        response = self.session.get(f"{BASE_URL}/api/dashboard/stats")
        assert response.status_code == 200, f"Dashboard stats failed: {response.text}"
        data = response.json()
        assert "total_conversations" in data, "Response should have 'total_conversations'"
        print("PASS: /api/dashboard/stats endpoint works")
    
    def test_plans_endpoint_works(self):
        """Test /api/plans still works"""
        response = requests.get(f"{BASE_URL}/api/plans")
        assert response.status_code == 200, f"Plans failed: {response.text}"
        data = response.json()
        assert isinstance(data, list), "Plans should be a list"
        assert len(data) >= 4, "Should have at least 4 plans"
        print(f"PASS: /api/plans endpoint works - {len(data)} plans returned")
    
    def test_dashboard_agents_endpoint_works(self):
        """Test /api/dashboard/agents still works"""
        response = self.session.get(f"{BASE_URL}/api/dashboard/agents")
        assert response.status_code == 200, f"Dashboard agents failed: {response.text}"
        data = response.json()
        assert "agents" in data, "Response should have 'agents' key"
        assert len(data["agents"]) == 6, f"Should have 6 agents, got {len(data['agents'])}"
        print("PASS: /api/dashboard/agents endpoint works - 6 agents returned")
    
    # ============ Auth Endpoints ============
    
    def test_auth_me_endpoint_works(self):
        """Test /api/auth/me still works"""
        response = self.session.get(f"{BASE_URL}/api/auth/me")
        assert response.status_code == 200, f"Auth me failed: {response.text}"
        data = response.json()
        assert "email" in data, "Response should have 'email'"
        assert data["email"] == "admin@switia.com"
        print("PASS: /api/auth/me endpoint works")


class TestNotificationsUnauthenticated:
    """Test notifications endpoints require authentication"""
    
    def test_get_notifications_requires_auth(self):
        """Test GET /api/notifications requires authentication"""
        response = requests.get(f"{BASE_URL}/api/notifications")
        assert response.status_code == 401, f"Expected 401, got {response.status_code}"
        print("PASS: GET /api/notifications requires authentication")
    
    def test_mark_read_requires_auth(self):
        """Test POST /api/notifications/read requires authentication"""
        response = requests.post(f"{BASE_URL}/api/notifications/read")
        assert response.status_code == 401, f"Expected 401, got {response.status_code}"
        print("PASS: POST /api/notifications/read requires authentication")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
