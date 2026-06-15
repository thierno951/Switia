"""
Iteration 6 Tests: Switia Rebranding & New Features
- 4 pricing plans (Gratuit/Pro 49€/PME 99€/Entreprise 199€)
- Enterprise plan has contact_only=true
- Onboarding endpoints (complete/reset)
- Auth with new admin@switia.com credentials
"""
import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

class TestAuthSwitia:
    """Test authentication with new Switia admin credentials"""
    
    def test_login_admin_switia(self):
        """Login with admin@switia.com / admin123"""
        response = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": "admin@switia.com",
            "password": "admin123"
        })
        assert response.status_code == 200, f"Login failed: {response.text}"
        data = response.json()
        assert data["email"] == "admin@switia.com"
        assert "id" in data
        print(f"✓ Login with admin@switia.com successful, user_id: {data['id']}")
        return response.cookies

    def test_auth_me_returns_onboarding_field(self):
        """GET /api/auth/me returns onboarding_completed field"""
        # Login first
        login_resp = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": "admin@switia.com",
            "password": "admin123"
        })
        cookies = login_resp.cookies
        
        # Get user info
        response = requests.get(f"{BASE_URL}/api/auth/me", cookies=cookies)
        assert response.status_code == 200
        data = response.json()
        assert "onboarding_completed" in data, "onboarding_completed field missing from /api/auth/me"
        assert isinstance(data["onboarding_completed"], bool)
        print(f"✓ /api/auth/me returns onboarding_completed: {data['onboarding_completed']}")


class TestPlansEndpoint:
    """Test GET /api/plans returns 4 plans with correct prices"""
    
    def test_plans_returns_4_plans(self):
        """GET /api/plans returns exactly 4 plans"""
        response = requests.get(f"{BASE_URL}/api/plans")
        assert response.status_code == 200
        plans = response.json()
        assert len(plans) == 4, f"Expected 4 plans, got {len(plans)}"
        print("✓ /api/plans returns 4 plans")
    
    def test_plan_free_correct(self):
        """Free plan: id=free, price=0"""
        response = requests.get(f"{BASE_URL}/api/plans")
        plans = {p["id"]: p for p in response.json()}
        
        assert "free" in plans
        free = plans["free"]
        assert free["name"] == "Gratuit"
        assert free["price"] == 0.0
        assert free["contact_only"] is False
        print("✓ Free plan: Gratuit, 0€")
    
    def test_plan_pro_correct(self):
        """Pro plan: id=pro, price=49€, popular=true"""
        response = requests.get(f"{BASE_URL}/api/plans")
        plans = {p["id"]: p for p in response.json()}
        
        assert "pro" in plans
        pro = plans["pro"]
        assert pro["name"] == "Pro"
        assert pro["price"] == 49.0
        assert pro["popular"] is True
        assert pro["contact_only"] is False
        print("✓ Pro plan: 49€, popular=true")
    
    def test_plan_pme_correct(self):
        """PME plan: id=pme, price=99€"""
        response = requests.get(f"{BASE_URL}/api/plans")
        plans = {p["id"]: p for p in response.json()}
        
        assert "pme" in plans
        pme = plans["pme"]
        assert pme["name"] == "PME"
        assert pme["price"] == 99.0
        assert pme["contact_only"] is False
        # Check PME-specific features
        features_str = " ".join(pme["features"]).lower()
        assert "widget" in features_str or "équipe" in features_str, "PME should have widget or team features"
        print("✓ PME plan: 99€, features include widget/team")
    
    def test_plan_enterprise_contact_only(self):
        """Enterprise plan: id=enterprise, price=199€, contact_only=true"""
        response = requests.get(f"{BASE_URL}/api/plans")
        plans = {p["id"]: p for p in response.json()}
        
        assert "enterprise" in plans
        enterprise = plans["enterprise"]
        assert enterprise["name"] == "Entreprise"
        assert enterprise["price"] == 199.0
        assert enterprise["contact_only"] is True, "Enterprise plan must have contact_only=true"
        print("✓ Enterprise plan: 199€, contact_only=true")


class TestOnboardingEndpoints:
    """Test onboarding complete/reset endpoints"""
    
    @pytest.fixture
    def auth_cookies(self):
        """Get authenticated session cookies"""
        response = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": "admin@switia.com",
            "password": "admin123"
        })
        return response.cookies
    
    def test_onboarding_complete(self, auth_cookies):
        """POST /api/onboarding/complete marks onboarding as complete"""
        response = requests.post(f"{BASE_URL}/api/onboarding/complete", cookies=auth_cookies)
        assert response.status_code == 200
        data = response.json()
        assert "message" in data
        print(f"✓ POST /api/onboarding/complete: {data['message']}")
        
        # Verify via /api/auth/me
        me_resp = requests.get(f"{BASE_URL}/api/auth/me", cookies=auth_cookies)
        assert me_resp.json()["onboarding_completed"] is True
        print("✓ Verified onboarding_completed=true via /api/auth/me")
    
    def test_onboarding_reset(self, auth_cookies):
        """POST /api/onboarding/reset resets onboarding"""
        response = requests.post(f"{BASE_URL}/api/onboarding/reset", cookies=auth_cookies)
        assert response.status_code == 200
        data = response.json()
        assert "message" in data
        print(f"✓ POST /api/onboarding/reset: {data['message']}")
        
        # Verify via /api/auth/me
        me_resp = requests.get(f"{BASE_URL}/api/auth/me", cookies=auth_cookies)
        assert me_resp.json()["onboarding_completed"] is False
        print("✓ Verified onboarding_completed=false via /api/auth/me")
    
    def test_onboarding_requires_auth(self):
        """Onboarding endpoints require authentication"""
        # Complete without auth
        resp1 = requests.post(f"{BASE_URL}/api/onboarding/complete")
        assert resp1.status_code == 401
        
        # Reset without auth
        resp2 = requests.post(f"{BASE_URL}/api/onboarding/reset")
        assert resp2.status_code == 401
        print("✓ Onboarding endpoints require authentication (401 without cookies)")


class TestSubscriptionEndpoint:
    """Test subscription endpoint"""
    
    @pytest.fixture
    def auth_cookies(self):
        response = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": "admin@switia.com",
            "password": "admin123"
        })
        return response.cookies
    
    def test_subscription_returns_plan_info(self, auth_cookies):
        """GET /api/subscription returns current plan info"""
        response = requests.get(f"{BASE_URL}/api/subscription", cookies=auth_cookies)
        assert response.status_code == 200
        data = response.json()
        
        # Check required fields
        assert "plan_id" in data
        assert "plan_name" in data
        assert "conversations_used" in data
        assert "conversations_limit" in data
        assert "analyses_used" in data
        assert "analyses_limit" in data
        print(f"✓ GET /api/subscription returns plan: {data['plan_name']}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
