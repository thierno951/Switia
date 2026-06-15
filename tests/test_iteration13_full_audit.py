"""
Iteration 13: Full Application Audit - Backend API Tests
Tests all critical endpoints for the Switia SaaS platform.
"""
import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

class TestAuthEndpoints:
    """Authentication flow tests"""
    
    def test_login_success(self):
        """Test login with admin credentials"""
        response = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": "admin@switia.com",
            "password": "admin123"
        })
        assert response.status_code == 200, f"Login failed: {response.text}"
        data = response.json()
        assert "user" in data
        assert data["user"]["email"] == "admin@switia.com"
        # Store cookies for subsequent tests
        self.__class__.cookies = response.cookies
        print(f"✅ Login success: {data['user']['email']}")
    
    def test_login_invalid_credentials(self):
        """Test login with wrong password"""
        response = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": "admin@switia.com",
            "password": "wrongpassword"
        })
        assert response.status_code == 401
        print("✅ Invalid credentials returns 401")
    
    def test_auth_me_authenticated(self):
        """Test /api/auth/me with valid session"""
        response = requests.get(f"{BASE_URL}/api/auth/me", cookies=getattr(self.__class__, 'cookies', {}))
        assert response.status_code == 200
        data = response.json()
        assert data["email"] == "admin@switia.com"
        print(f"✅ /api/auth/me returns user: {data['email']}")
    
    def test_auth_me_unauthenticated(self):
        """Test /api/auth/me without session"""
        response = requests.get(f"{BASE_URL}/api/auth/me")
        assert response.status_code == 401
        print("✅ /api/auth/me without auth returns 401")


class TestDashboardEndpoints:
    """Dashboard API tests"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Login before each test"""
        response = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": "admin@switia.com",
            "password": "admin123"
        })
        self.cookies = response.cookies
    
    def test_dashboard_stats(self):
        """Test dashboard stats endpoint"""
        response = requests.get(f"{BASE_URL}/api/dashboard/stats", cookies=self.cookies)
        assert response.status_code == 200
        data = response.json()
        assert "total_conversations" in data
        assert "total_tickets" in data
        print(f"✅ Dashboard stats: {data.get('total_conversations')} conversations")
    
    def test_dashboard_time_saved(self):
        """Test time-saved endpoint"""
        response = requests.get(f"{BASE_URL}/api/dashboard/time-saved", cookies=self.cookies)
        assert response.status_code == 200
        data = response.json()
        assert "messages_handled" in data
        assert "hours_saved" in data
        assert "cost_saved_eur" in data
        print(f"✅ Time saved: {data['hours_saved']}h, {data['cost_saved_eur']}€")
    
    def test_dashboard_time_saved_history(self):
        """Test time-saved history endpoint"""
        response = requests.get(f"{BASE_URL}/api/dashboard/time-saved/history?days=30", cookies=self.cookies)
        assert response.status_code == 200
        data = response.json()
        assert "series" in data
        assert "current_month_messages" in data
        print(f"✅ Time saved history: {len(data['series'])} days")
    
    def test_dashboard_agents(self):
        """Test agents stats endpoint"""
        response = requests.get(f"{BASE_URL}/api/dashboard/agents", cookies=self.cookies)
        assert response.status_code == 200
        data = response.json()
        assert "agents" in data
        assert len(data["agents"]) >= 6  # 6 agent types
        print(f"✅ Agent stats: {len(data['agents'])} agents")


class TestCampaignsEndpoints:
    """Campaigns CRUD tests"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Login before each test"""
        response = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": "admin@switia.com",
            "password": "admin123"
        })
        self.cookies = response.cookies
    
    def test_campaigns_list(self):
        """Test campaigns list endpoint"""
        response = requests.get(f"{BASE_URL}/api/campaigns", cookies=self.cookies)
        assert response.status_code == 200
        data = response.json()
        assert "campaigns" in data
        print(f"✅ Campaigns list: {len(data['campaigns'])} campaigns")
    
    def test_campaigns_create_draft(self):
        """Test creating a draft campaign"""
        response = requests.post(f"{BASE_URL}/api/campaigns", json={
            "name": "TEST_Audit_Campaign",
            "channel": "whatsapp",
            "message": "Test message {name}",
            "recipients": [
                {"phone": "+33612345678", "name": "Test User 1"},
                {"phone": "+33698765432", "name": "Test User 2"}
            ]
        }, cookies=self.cookies)
        assert response.status_code == 201, f"Create failed: {response.text}"
        data = response.json()
        assert "id" in data
        self.__class__.test_campaign_id = data["id"]
        print(f"✅ Campaign created: {data['id']}")
    
    def test_campaigns_get_detail(self):
        """Test getting campaign detail"""
        campaign_id = getattr(self.__class__, 'test_campaign_id', None)
        if not campaign_id:
            pytest.skip("No campaign created")
        response = requests.get(f"{BASE_URL}/api/campaigns/{campaign_id}", cookies=self.cookies)
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "TEST_Audit_Campaign"
        assert len(data["recipients"]) == 2
        print(f"✅ Campaign detail: {data['name']} with {len(data['recipients'])} recipients")
    
    def test_campaigns_analytics(self):
        """Test campaign analytics endpoint"""
        campaign_id = getattr(self.__class__, 'test_campaign_id', None)
        if not campaign_id:
            pytest.skip("No campaign created")
        response = requests.get(f"{BASE_URL}/api/campaigns/{campaign_id}/analytics", cookies=self.cookies)
        assert response.status_code == 200
        data = response.json()
        assert "sent" in data
        assert "delivered" in data
        print(f"✅ Campaign analytics: sent={data['sent']}, delivered={data['delivered']}")
    
    def test_campaigns_delete(self):
        """Test deleting campaign"""
        campaign_id = getattr(self.__class__, 'test_campaign_id', None)
        if not campaign_id:
            pytest.skip("No campaign created")
        response = requests.delete(f"{BASE_URL}/api/campaigns/{campaign_id}", cookies=self.cookies)
        assert response.status_code == 200
        print(f"✅ Campaign deleted: {campaign_id}")
    
    def test_twilio_capabilities(self):
        """Test Twilio capabilities endpoint"""
        response = requests.get(f"{BASE_URL}/api/channels/twilio/capabilities", cookies=self.cookies)
        assert response.status_code == 200
        data = response.json()
        assert "whatsapp" in data
        assert "sms" in data
        # SMS should be false since TWILIO_SMS_FROM is not set
        assert data["whatsapp"] == True
        print(f"✅ Twilio capabilities: whatsapp={data['whatsapp']}, sms={data['sms']}")


class TestShowcaseEndpoints:
    """Public showcase and opt-in tests"""
    
    def test_public_showcase_no_auth(self):
        """Test public showcase endpoint without auth"""
        response = requests.get(f"{BASE_URL}/api/public/showcase?limit=9")
        assert response.status_code == 200
        data = response.json()
        assert "platform" in data
        assert "testimonials" in data
        print(f"✅ Public showcase: {len(data['testimonials'])} testimonials")
    
    def test_roi_badge_js(self):
        """Test ROI badge JS snippet endpoint"""
        response = requests.get(f"{BASE_URL}/api/public/roi-badge.js")
        assert response.status_code == 200
        assert "application/javascript" in response.headers.get("content-type", "")
        assert "switia" in response.text.lower()
        print("✅ ROI badge JS returns valid JavaScript")
    
    def test_showcase_optin_authenticated(self):
        """Test showcase opt-in endpoint with auth"""
        login = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": "admin@switia.com",
            "password": "admin123"
        })
        cookies = login.cookies
        
        response = requests.get(f"{BASE_URL}/api/showcase/opt-in", cookies=cookies)
        assert response.status_code == 200
        data = response.json()
        assert "opt_in" in data
        print(f"✅ Showcase opt-in: opt_in={data['opt_in']}")
    
    def test_showcase_optin_unauthenticated(self):
        """Test showcase opt-in without auth"""
        response = requests.get(f"{BASE_URL}/api/showcase/opt-in")
        assert response.status_code == 401
        print("✅ Showcase opt-in without auth returns 401")


class TestChannelsEndpoints:
    """Channels configuration tests"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Login before each test"""
        response = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": "admin@switia.com",
            "password": "admin123"
        })
        self.cookies = response.cookies
    
    def test_channels_get(self):
        """Test getting channels configuration"""
        response = requests.get(f"{BASE_URL}/api/channels", cookies=self.cookies)
        assert response.status_code == 200
        data = response.json()
        assert "email" in data
        assert "whatsapp" in data
        print(f"✅ Channels config loaded")
    
    def test_gmail_oauth_status(self):
        """Test Gmail OAuth status endpoint"""
        response = requests.get(f"{BASE_URL}/api/oauth/gmail/status", cookies=self.cookies)
        assert response.status_code == 200
        data = response.json()
        assert "connected" in data
        print(f"✅ Gmail OAuth status: connected={data['connected']}")
    
    def test_gmail_oauth_login_redirect(self):
        """Test Gmail OAuth login returns auth URL"""
        response = requests.get(f"{BASE_URL}/api/oauth/gmail/login", cookies=self.cookies, allow_redirects=False)
        # Should return 200 with auth_url or redirect
        assert response.status_code in [200, 302, 307]
        if response.status_code == 200:
            data = response.json()
            assert "auth_url" in data
            assert "google" in data["auth_url"].lower()
            print(f"✅ Gmail OAuth login returns Google auth URL")
        else:
            print(f"✅ Gmail OAuth login redirects (status {response.status_code})")


class TestSecurityEndpoints:
    """Security and 2FA tests"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Login before each test"""
        response = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": "admin@switia.com",
            "password": "admin123"
        })
        self.cookies = response.cookies
    
    def test_2fa_status(self):
        """Test 2FA status endpoint"""
        response = requests.get(f"{BASE_URL}/api/security/2fa/status", cookies=self.cookies)
        assert response.status_code == 200
        data = response.json()
        assert "enabled" in data
        print(f"✅ 2FA status: enabled={data['enabled']}")


class TestWidgetEndpoints:
    """Widget API tests"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Login before each test"""
        response = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": "admin@switia.com",
            "password": "admin123"
        })
        self.cookies = response.cookies
    
    def test_widget_keys_list(self):
        """Test listing widget keys"""
        response = requests.get(f"{BASE_URL}/api/widget/keys", cookies=self.cookies)
        assert response.status_code == 200
        data = response.json()
        assert "keys" in data
        print(f"✅ Widget keys: {len(data['keys'])} keys")
    
    def test_widget_embed_js(self):
        """Test widget embed.js endpoint"""
        response = requests.get(f"{BASE_URL}/api/widget/embed.js")
        assert response.status_code == 200
        assert "application/javascript" in response.headers.get("content-type", "")
        print("✅ Widget embed.js returns valid JavaScript")


class TestPricingEndpoints:
    """Pricing and subscription tests"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Login before each test"""
        response = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": "admin@switia.com",
            "password": "admin123"
        })
        self.cookies = response.cookies
    
    def test_plans_list(self):
        """Test listing subscription plans"""
        response = requests.get(f"{BASE_URL}/api/plans", cookies=self.cookies)
        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 3  # At least free, pro, enterprise
        print(f"✅ Plans: {len(data)} plans available")
    
    def test_subscription_status(self):
        """Test user subscription status"""
        response = requests.get(f"{BASE_URL}/api/subscription", cookies=self.cookies)
        assert response.status_code == 200
        data = response.json()
        assert "plan_id" in data
        assert "plan_name" in data
        print(f"✅ Subscription: {data['plan_name']}")


class TestNotificationsEndpoints:
    """Notifications tests"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Login before each test"""
        response = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": "admin@switia.com",
            "password": "admin123"
        })
        self.cookies = response.cookies
    
    def test_notifications_list(self):
        """Test listing notifications"""
        response = requests.get(f"{BASE_URL}/api/notifications", cookies=self.cookies)
        assert response.status_code == 200
        data = response.json()
        assert "notifications" in data
        assert "unread_count" in data
        print(f"✅ Notifications: {data['unread_count']} unread")


class TestSAVEndpoints:
    """SAV (Support) tickets tests"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Login before each test"""
        response = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": "admin@switia.com",
            "password": "admin123"
        })
        self.cookies = response.cookies
    
    def test_sav_tickets_list(self):
        """Test listing SAV tickets"""
        response = requests.get(f"{BASE_URL}/api/sav/tickets", cookies=self.cookies)
        assert response.status_code == 200
        data = response.json()
        assert "tickets" in data
        print(f"✅ SAV tickets: {len(data['tickets'])} tickets")
    
    def test_sav_unread_count(self):
        """Test SAV unread count"""
        response = requests.get(f"{BASE_URL}/api/sav/unread-count", cookies=self.cookies)
        assert response.status_code == 200
        data = response.json()
        assert "count" in data
        print(f"✅ SAV unread: {data['count']}")


class TestWhitelabelEndpoints:
    """White-label settings tests"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Login before each test"""
        response = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": "admin@switia.com",
            "password": "admin123"
        })
        self.cookies = response.cookies
    
    def test_whitelabel_get(self):
        """Test getting white-label settings"""
        response = requests.get(f"{BASE_URL}/api/settings/whitelabel", cookies=self.cookies)
        assert response.status_code == 200
        data = response.json()
        # Should have brand_name and primary_color
        print(f"✅ White-label settings loaded")


class TestOnboardingEndpoints:
    """Onboarding tests"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Login before each test"""
        response = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": "admin@switia.com",
            "password": "admin123"
        })
        self.cookies = response.cookies
    
    def test_onboarding_status(self):
        """Test onboarding status endpoint"""
        response = requests.get(f"{BASE_URL}/api/onboarding/status", cookies=self.cookies)
        assert response.status_code == 200
        data = response.json()
        assert "completed" in data or "steps" in data or "show_guide" in data
        print(f"✅ Onboarding status loaded")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
