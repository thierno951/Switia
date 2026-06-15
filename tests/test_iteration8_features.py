"""
Iteration 8 Backend Tests - New AI Agents & White-label Features
Tests:
1. Login with admin@switia.com / admin123
2. POST /api/agents/commercial/chat - Commercial agent responds
3. POST /api/agents/rh/chat - RH agent responds
4. POST /api/agents/finance/chat - Finance agent responds
5. POST /api/agents/marketing/chat - Marketing agent responds
6. GET /api/agents/commercial/sessions - lists sessions
7. GET /api/agents/commercial/history/{session_id} - loads messages
8. POST /api/agents/invalid_type/chat - returns 404
9. GET /api/settings/whitelabel - returns default settings
10. PUT /api/settings/whitelabel with brand_name and primary_color - updates settings
11. All existing endpoints still work: /api/support/chat, /api/plans, /api/dashboard/stats, /api/widget/keys
"""

import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

class TestAuth:
    """Test authentication with admin credentials"""
    
    @pytest.fixture(scope="class")
    def session(self):
        """Create a session for cookie persistence"""
        return requests.Session()
    
    def test_login_admin(self, session):
        """Test 1: Login with admin@switia.com / admin123"""
        response = session.post(f"{BASE_URL}/api/auth/login", json={
            "email": "admin@switia.com",
            "password": "admin123"
        })
        assert response.status_code == 200, f"Login failed: {response.text}"
        data = response.json()
        assert "id" in data
        assert data["email"] == "admin@switia.com"
        assert data["role"] == "admin"
        print("✓ Login successful for admin@switia.com")


class TestNewAgents:
    """Test new AI agent endpoints (Commercial, RH, Finance, Marketing)"""
    
    @pytest.fixture(scope="class")
    def auth_session(self):
        """Create authenticated session"""
        session = requests.Session()
        response = session.post(f"{BASE_URL}/api/auth/login", json={
            "email": "admin@switia.com",
            "password": "admin123"
        })
        assert response.status_code == 200, "Auth failed"
        return session
    
    def test_commercial_agent_chat(self, auth_session):
        """Test 2: POST /api/agents/commercial/chat - Commercial agent responds"""
        response = auth_session.post(f"{BASE_URL}/api/agents/commercial/chat", json={
            "message": "Bonjour, j'ai besoin d'aide pour qualifier un lead"
        })
        assert response.status_code == 200, f"Commercial chat failed: {response.text}"
        data = response.json()
        assert "response" in data
        assert "session_id" in data
        assert len(data["response"]) > 0
        print(f"✓ Commercial agent responded: {data['response'][:100]}...")
        return data["session_id"]
    
    def test_rh_agent_chat(self, auth_session):
        """Test 3: POST /api/agents/rh/chat - RH agent responds"""
        response = auth_session.post(f"{BASE_URL}/api/agents/rh/chat", json={
            "message": "Comment demander des congés ?"
        })
        assert response.status_code == 200, f"RH chat failed: {response.text}"
        data = response.json()
        assert "response" in data
        assert "session_id" in data
        assert len(data["response"]) > 0
        print(f"✓ RH agent responded: {data['response'][:100]}...")
    
    def test_finance_agent_chat(self, auth_session):
        """Test 4: POST /api/agents/finance/chat - Finance agent responds"""
        response = auth_session.post(f"{BASE_URL}/api/agents/finance/chat", json={
            "message": "Comment créer une facture ?"
        })
        assert response.status_code == 200, f"Finance chat failed: {response.text}"
        data = response.json()
        assert "response" in data
        assert "session_id" in data
        assert len(data["response"]) > 0
        print(f"✓ Finance agent responded: {data['response'][:100]}...")
    
    def test_marketing_agent_chat(self, auth_session):
        """Test 5: POST /api/agents/marketing/chat - Marketing agent responds"""
        response = auth_session.post(f"{BASE_URL}/api/agents/marketing/chat", json={
            "message": "Aide-moi à créer une campagne email"
        })
        assert response.status_code == 200, f"Marketing chat failed: {response.text}"
        data = response.json()
        assert "response" in data
        assert "session_id" in data
        assert len(data["response"]) > 0
        print(f"✓ Marketing agent responded: {data['response'][:100]}...")
    
    def test_commercial_sessions(self, auth_session):
        """Test 6: GET /api/agents/commercial/sessions - lists sessions"""
        # First create a session by chatting
        chat_response = auth_session.post(f"{BASE_URL}/api/agents/commercial/chat", json={
            "message": "Test session creation"
        })
        assert chat_response.status_code == 200
        session_id = chat_response.json()["session_id"]
        
        # Now get sessions
        response = auth_session.get(f"{BASE_URL}/api/agents/commercial/sessions")
        assert response.status_code == 200, f"Get sessions failed: {response.text}"
        data = response.json()
        assert "sessions" in data
        assert isinstance(data["sessions"], list)
        # Verify our session is in the list
        session_ids = [s["session_id"] for s in data["sessions"]]
        assert session_id in session_ids, "Created session not found in sessions list"
        print(f"✓ Commercial sessions returned {len(data['sessions'])} sessions")
        return session_id
    
    def test_commercial_history(self, auth_session):
        """Test 7: GET /api/agents/commercial/history/{session_id} - loads messages"""
        # First create a session
        chat_response = auth_session.post(f"{BASE_URL}/api/agents/commercial/chat", json={
            "message": "Test history message"
        })
        assert chat_response.status_code == 200
        session_id = chat_response.json()["session_id"]
        
        # Get history
        response = auth_session.get(f"{BASE_URL}/api/agents/commercial/history/{session_id}")
        assert response.status_code == 200, f"Get history failed: {response.text}"
        data = response.json()
        assert "history" in data
        assert isinstance(data["history"], list)
        assert len(data["history"]) >= 2  # At least user message + agent response
        # Verify message structure
        for msg in data["history"]:
            assert "message" in msg
            assert "role" in msg
            assert msg["role"] in ["user", "agent"]
        print(f"✓ Commercial history returned {len(data['history'])} messages")
    
    def test_invalid_agent_type(self, auth_session):
        """Test 8: POST /api/agents/invalid_type/chat - returns 404"""
        response = auth_session.post(f"{BASE_URL}/api/agents/invalid_type/chat", json={
            "message": "This should fail"
        })
        assert response.status_code == 404, f"Expected 404, got {response.status_code}"
        print("✓ Invalid agent type correctly returns 404")


class TestWhiteLabel:
    """Test white-label settings endpoints"""
    
    @pytest.fixture(scope="class")
    def auth_session(self):
        """Create authenticated session"""
        session = requests.Session()
        response = session.post(f"{BASE_URL}/api/auth/login", json={
            "email": "admin@switia.com",
            "password": "admin123"
        })
        assert response.status_code == 200, "Auth failed"
        return session
    
    def test_get_whitelabel_defaults(self, auth_session):
        """Test 9: GET /api/settings/whitelabel - returns default settings"""
        response = auth_session.get(f"{BASE_URL}/api/settings/whitelabel")
        assert response.status_code == 200, f"Get whitelabel failed: {response.text}"
        data = response.json()
        # Should have default values
        assert "brand_name" in data
        assert "primary_color" in data
        assert "logo_url" in data
        print(f"✓ Whitelabel defaults: brand_name={data['brand_name']}, primary_color={data['primary_color']}")
    
    def test_update_whitelabel(self, auth_session):
        """Test 10: PUT /api/settings/whitelabel with brand_name and primary_color - updates settings"""
        update_data = {
            "brand_name": "TEST_MyBrand",
            "primary_color": "#FF5733",
            "logo_url": "https://example.com/logo.png"
        }
        response = auth_session.put(f"{BASE_URL}/api/settings/whitelabel", json=update_data)
        assert response.status_code == 200, f"Update whitelabel failed: {response.text}"
        data = response.json()
        assert "message" in data
        
        # Verify by getting settings again
        get_response = auth_session.get(f"{BASE_URL}/api/settings/whitelabel")
        assert get_response.status_code == 200
        get_data = get_response.json()
        assert get_data["brand_name"] == "TEST_MyBrand"
        assert get_data["primary_color"] == "#FF5733"
        assert get_data["logo_url"] == "https://example.com/logo.png"
        print("✓ Whitelabel settings updated and verified")
        
        # Reset to defaults
        auth_session.put(f"{BASE_URL}/api/settings/whitelabel", json={
            "brand_name": "Switia",
            "primary_color": "#2563EB",
            "logo_url": ""
        })


class TestExistingEndpoints:
    """Test 11: All existing endpoints still work"""
    
    @pytest.fixture(scope="class")
    def auth_session(self):
        """Create authenticated session"""
        session = requests.Session()
        response = session.post(f"{BASE_URL}/api/auth/login", json={
            "email": "admin@switia.com",
            "password": "admin123"
        })
        assert response.status_code == 200, "Auth failed"
        return session
    
    def test_support_chat(self, auth_session):
        """Test /api/support/chat still works"""
        response = auth_session.post(f"{BASE_URL}/api/support/chat", json={
            "message": "Test support message"
        })
        assert response.status_code == 200, f"Support chat failed: {response.text}"
        data = response.json()
        assert "response" in data
        assert "session_id" in data
        print("✓ Support chat endpoint works")
    
    def test_plans(self, auth_session):
        """Test /api/plans still works"""
        response = auth_session.get(f"{BASE_URL}/api/plans")
        assert response.status_code == 200, f"Plans failed: {response.text}"
        data = response.json()
        assert isinstance(data, list)
        assert len(data) >= 4  # free, pro, pme, enterprise
        plan_ids = [p["id"] for p in data]
        assert "free" in plan_ids
        assert "pro" in plan_ids
        print(f"✓ Plans endpoint returns {len(data)} plans")
    
    def test_dashboard_stats(self, auth_session):
        """Test /api/dashboard/stats still works"""
        response = auth_session.get(f"{BASE_URL}/api/dashboard/stats")
        assert response.status_code == 200, f"Dashboard stats failed: {response.text}"
        data = response.json()
        assert "total_conversations" in data
        assert "total_tickets" in data
        assert "total_analyses" in data
        print("✓ Dashboard stats endpoint works")
    
    def test_widget_keys(self, auth_session):
        """Test /api/widget/keys still works"""
        response = auth_session.get(f"{BASE_URL}/api/widget/keys")
        assert response.status_code == 200, f"Widget keys failed: {response.text}"
        data = response.json()
        assert "keys" in data
        print(f"✓ Widget keys endpoint returns {len(data['keys'])} keys")


class TestAgentSessionsAndHistory:
    """Additional tests for all agent types sessions and history"""
    
    @pytest.fixture(scope="class")
    def auth_session(self):
        """Create authenticated session"""
        session = requests.Session()
        response = session.post(f"{BASE_URL}/api/auth/login", json={
            "email": "admin@switia.com",
            "password": "admin123"
        })
        assert response.status_code == 200, "Auth failed"
        return session
    
    def test_rh_sessions(self, auth_session):
        """Test GET /api/agents/rh/sessions"""
        response = auth_session.get(f"{BASE_URL}/api/agents/rh/sessions")
        assert response.status_code == 200
        data = response.json()
        assert "sessions" in data
        print("✓ RH sessions endpoint works")
    
    def test_finance_sessions(self, auth_session):
        """Test GET /api/agents/finance/sessions"""
        response = auth_session.get(f"{BASE_URL}/api/agents/finance/sessions")
        assert response.status_code == 200
        data = response.json()
        assert "sessions" in data
        print("✓ Finance sessions endpoint works")
    
    def test_marketing_sessions(self, auth_session):
        """Test GET /api/agents/marketing/sessions"""
        response = auth_session.get(f"{BASE_URL}/api/agents/marketing/sessions")
        assert response.status_code == 200
        data = response.json()
        assert "sessions" in data
        print("✓ Marketing sessions endpoint works")
    
    def test_invalid_agent_sessions(self, auth_session):
        """Test GET /api/agents/invalid/sessions returns 404"""
        response = auth_session.get(f"{BASE_URL}/api/agents/invalid/sessions")
        assert response.status_code == 404
        print("✓ Invalid agent sessions correctly returns 404")
    
    def test_invalid_agent_history(self, auth_session):
        """Test GET /api/agents/invalid/history/xxx returns 404"""
        response = auth_session.get(f"{BASE_URL}/api/agents/invalid/history/test-session")
        assert response.status_code == 404
        print("✓ Invalid agent history correctly returns 404")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
