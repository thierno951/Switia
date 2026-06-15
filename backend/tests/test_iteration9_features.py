"""
Iteration 9 Tests: Landing page, Widget branding, Dashboard agents, PME→Business rename, French translations
"""
import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

class TestLandingPageAndPublicEndpoints:
    """Test public endpoints that don't require authentication"""
    
    def test_root_returns_200(self):
        """Landing page at / should load without auth"""
        response = requests.get(f"{BASE_URL}/")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        print("PASS: GET / returns 200")
    
    def test_plans_endpoint_returns_business_not_pme(self):
        """GET /api/plans should have 'Business' plan (not 'PME')"""
        response = requests.get(f"{BASE_URL}/api/plans")
        assert response.status_code == 200
        plans = response.json()
        
        # Check that we have 4 plans
        assert len(plans) == 4, f"Expected 4 plans, got {len(plans)}"
        
        # Find the pme plan and verify it's named 'Business'
        pme_plan = next((p for p in plans if p['id'] == 'pme'), None)
        assert pme_plan is not None, "Plan with id 'pme' not found"
        assert pme_plan['name'] == 'Business', f"Expected 'Business', got '{pme_plan['name']}'"
        
        # Verify no plan is named 'PME'
        plan_names = [p['name'] for p in plans]
        assert 'PME' not in plan_names, f"Found 'PME' in plan names: {plan_names}"
        
        print(f"PASS: GET /api/plans returns Business plan (not PME). Plans: {plan_names}")
    
    def test_widget_branding_without_key_returns_defaults(self):
        """GET /api/widget/branding without key returns defaults"""
        response = requests.get(f"{BASE_URL}/api/widget/branding")
        assert response.status_code == 200
        data = response.json()
        
        assert data.get('brand_name') == 'Switia', f"Expected 'Switia', got '{data.get('brand_name')}'"
        assert data.get('primary_color') == '#2563EB', f"Expected '#2563EB', got '{data.get('primary_color')}'"
        
        print(f"PASS: GET /api/widget/branding without key returns defaults: {data}")
    
    def test_widget_branding_with_invalid_key_returns_defaults(self):
        """GET /api/widget/branding with invalid key returns defaults"""
        response = requests.get(f"{BASE_URL}/api/widget/branding?key=invalid_key_12345")
        assert response.status_code == 200
        data = response.json()
        
        assert data.get('brand_name') == 'Switia'
        assert data.get('primary_color') == '#2563EB'
        
        print("PASS: GET /api/widget/branding with invalid key returns defaults")
    
    def test_embed_js_contains_branding_fetch(self):
        """GET /api/widget/embed.js should contain fetch to /api/widget/branding"""
        response = requests.get(f"{BASE_URL}/api/widget/embed.js")
        assert response.status_code == 200
        content = response.text
        
        # Check that embed.js fetches branding endpoint
        assert '/api/widget/branding' in content, "embed.js should fetch /api/widget/branding"
        assert 'brand_name' in content, "embed.js should use brand_name"
        assert 'primary_color' in content, "embed.js should use primary_color"
        
        print("PASS: GET /api/widget/embed.js contains dynamic white-label fetch")


class TestAuthenticatedEndpoints:
    """Test endpoints that require authentication"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Login and get session cookies"""
        self.session = requests.Session()
        login_response = self.session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": "admin@switia.com", "password": "admin123"}
        )
        if login_response.status_code != 200:
            pytest.skip(f"Login failed: {login_response.status_code} - {login_response.text}")
        self.user = login_response.json()
        print(f"Logged in as: {self.user.get('email')}")
    
    def test_dashboard_agents_returns_6_agents(self):
        """GET /api/dashboard/agents should return stats for 6 agents"""
        response = self.session.get(f"{BASE_URL}/api/dashboard/agents")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        
        data = response.json()
        agents = data.get('agents', [])
        
        # Should have exactly 6 agents
        assert len(agents) == 6, f"Expected 6 agents, got {len(agents)}"
        
        # Check all expected agent types are present
        expected_types = {'support', 'analysis', 'commercial', 'rh', 'finance', 'marketing'}
        actual_types = {a['type'] for a in agents}
        assert actual_types == expected_types, f"Expected types {expected_types}, got {actual_types}"
        
        # Each agent should have conversations count and last_activity
        for agent in agents:
            assert 'conversations' in agent, f"Agent {agent['type']} missing 'conversations'"
            assert 'last_activity' in agent, f"Agent {agent['type']} missing 'last_activity'"
            assert 'name' in agent, f"Agent {agent['type']} missing 'name'"
        
        print(f"PASS: GET /api/dashboard/agents returns 6 agents: {[a['type'] for a in agents]}")
    
    def test_support_chat_still_works(self):
        """Existing /api/support/chat endpoint should still work"""
        response = self.session.post(
            f"{BASE_URL}/api/support/chat",
            json={"message": "Test message for iteration 9"}
        )
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()
        assert 'response' in data, "Response should contain 'response' field"
        print("PASS: POST /api/support/chat still works")
    
    def test_commercial_agent_chat_still_works(self):
        """Existing /api/agents/commercial/chat endpoint should still work"""
        response = self.session.post(
            f"{BASE_URL}/api/agents/commercial/chat",
            json={"message": "Test commercial message"}
        )
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()
        assert 'response' in data, "Response should contain 'response' field"
        print("PASS: POST /api/agents/commercial/chat still works")
    
    def test_whitelabel_settings_still_works(self):
        """Existing /api/settings/whitelabel endpoint should still work"""
        response = self.session.get(f"{BASE_URL}/api/settings/whitelabel")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()
        assert 'brand_name' in data, "Response should contain 'brand_name'"
        assert 'primary_color' in data, "Response should contain 'primary_color'"
        print("PASS: GET /api/settings/whitelabel still works")
    
    def test_widget_branding_with_valid_key(self):
        """Test widget branding with a valid API key"""
        # First create a widget key
        create_response = self.session.post(
            f"{BASE_URL}/api/widget/keys",
            json={"name": "Test Key for Branding", "allowed_origins": ["*"]}
        )
        if create_response.status_code != 200:
            pytest.skip(f"Could not create widget key: {create_response.text}")
        
        key_data = create_response.json()
        api_key = key_data.get('api_key')
        
        # Now test branding endpoint with this key
        branding_response = requests.get(f"{BASE_URL}/api/widget/branding?key={api_key}")
        assert branding_response.status_code == 200
        data = branding_response.json()
        
        # Should return branding (defaults if no custom settings)
        assert 'brand_name' in data
        assert 'primary_color' in data
        
        print("PASS: GET /api/widget/branding with valid key returns branding data")


class TestDashboardStats:
    """Test dashboard stats endpoint"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        self.session = requests.Session()
        login_response = self.session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": "admin@switia.com", "password": "admin123"}
        )
        if login_response.status_code != 200:
            pytest.skip("Login failed")
    
    def test_dashboard_stats_endpoint(self):
        """GET /api/dashboard/stats should return stats"""
        response = self.session.get(f"{BASE_URL}/api/dashboard/stats")
        assert response.status_code == 200
        data = response.json()
        
        # Check expected fields
        expected_fields = ['total_conversations', 'total_tickets', 'total_analyses', 
                          'resolved_tickets', 'escalation_rate', 'widget_sessions']
        for field in expected_fields:
            assert field in data, f"Missing field: {field}"
        
        print("PASS: GET /api/dashboard/stats returns expected fields")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
