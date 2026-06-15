"""
Iteration 11 Tests: Public Showcase + ROI Badge + Outbound SMS/WhatsApp Campaigns

Features tested:
1. GET /api/public/showcase (no auth) - platform aggregates + anonymized testimonials
2. GET /api/public/roi-badge.js (no auth) - JS snippet (Content-Type application/javascript)
3. GET /api/showcase/my-card (auth) - user's sector + stats + share_text
4. POST /api/campaigns - create campaign with recipients
5. POST /api/campaigns validation - missing message, empty recipients, invalid channel
6. GET /api/campaigns - list user's campaigns
7. GET /api/campaigns/{id} - full campaign with recipients
8. POST /api/campaigns/{id}/send - trigger Twilio send (sandbox may fail gracefully)
9. DELETE /api/campaigns/{id} - remove campaign
10. Authorization: unauthenticated access to /api/campaigns* returns 401
"""

import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

@pytest.fixture(scope="module")
def api_client():
    """Shared requests session"""
    session = requests.Session()
    session.headers.update({"Content-Type": "application/json"})
    return session


@pytest.fixture(scope="module")
def auth_session(api_client):
    """Authenticated session with admin credentials"""
    login_resp = api_client.post(f"{BASE_URL}/api/auth/login", json={
        "email": "admin@switia.com",
        "password": "admin123"
    })
    assert login_resp.status_code == 200, f"Login failed: {login_resp.text}"
    # Session cookies are stored in api_client
    return api_client


class TestPublicShowcase:
    """Public showcase endpoints (no auth required)"""
    
    def test_public_showcase_no_auth(self, api_client):
        """GET /api/public/showcase returns platform aggregates + testimonials without auth"""
        response = api_client.get(f"{BASE_URL}/api/public/showcase")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        # Validate structure
        assert "platform" in data, "Missing 'platform' key"
        assert "testimonials" in data, "Missing 'testimonials' key"
        assert "generated_at" in data, "Missing 'generated_at' key"
        
        # Validate platform aggregates structure
        platform = data["platform"]
        assert "active_companies" in platform
        assert "total_messages_handled" in platform
        assert "total_hours_saved" in platform
        assert "total_workdays_saved" in platform
        assert "total_cost_saved_eur" in platform
        
        # Testimonials should be a list
        assert isinstance(data["testimonials"], list)
        print(f"Public showcase: {platform['active_companies']} companies, {platform['total_messages_handled']} messages")
    
    def test_public_showcase_with_limit(self, api_client):
        """GET /api/public/showcase?limit=5 respects limit parameter"""
        response = api_client.get(f"{BASE_URL}/api/public/showcase?limit=5")
        assert response.status_code == 200
        data = response.json()
        # Limit should be respected (max 5 testimonials)
        assert len(data["testimonials"]) <= 5
    
    def test_roi_badge_js_no_auth(self, api_client):
        """GET /api/public/roi-badge.js returns JS snippet with correct Content-Type"""
        response = api_client.get(f"{BASE_URL}/api/public/roi-badge.js")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        # Check Content-Type is application/javascript
        content_type = response.headers.get("Content-Type", "")
        assert "application/javascript" in content_type, f"Expected application/javascript, got {content_type}"
        
        # Check it's valid JS (contains expected patterns)
        js_content = response.text
        assert "(function()" in js_content or "function" in js_content, "JS content doesn't look like valid JavaScript"
        assert "switia" in js_content.lower() or "roi" in js_content.lower(), "JS content should reference Switia or ROI"
        print(f"ROI badge JS snippet length: {len(js_content)} chars")


class TestShowcaseMyCard:
    """Authenticated showcase/my-card endpoint"""
    
    def test_my_card_requires_auth(self, api_client):
        """GET /api/showcase/my-card without auth returns 401"""
        # Use a fresh session without cookies
        fresh_session = requests.Session()
        response = fresh_session.get(f"{BASE_URL}/api/showcase/my-card")
        assert response.status_code == 401, f"Expected 401 without auth, got {response.status_code}"
    
    def test_my_card_with_auth(self, auth_session):
        """GET /api/showcase/my-card returns user's ROI card with share_text"""
        response = auth_session.get(f"{BASE_URL}/api/showcase/my-card")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        # Validate structure
        assert "sector" in data, "Missing 'sector' key"
        assert "messages" in data, "Missing 'messages' key"
        assert "hours_saved" in data, "Missing 'hours_saved' key"
        assert "workdays_saved" in data, "Missing 'workdays_saved' key"
        assert "cost_saved_eur" in data, "Missing 'cost_saved_eur' key"
        assert "share_text" in data, "Missing 'share_text' key"
        
        # share_text should be a non-empty string for LinkedIn
        assert isinstance(data["share_text"], str)
        assert len(data["share_text"]) > 0, "share_text should not be empty"
        assert "Switia" in data["share_text"], "share_text should mention Switia"
        
        print(f"My card: sector={data['sector']}, messages={data['messages']}, hours={data['hours_saved']}")


class TestCampaignsAuth:
    """Campaign endpoints authorization tests"""
    
    def test_campaigns_list_requires_auth(self, api_client):
        """GET /api/campaigns without auth returns 401"""
        fresh_session = requests.Session()
        response = fresh_session.get(f"{BASE_URL}/api/campaigns")
        assert response.status_code == 401, f"Expected 401 without auth, got {response.status_code}"
    
    def test_campaigns_create_requires_auth(self, api_client):
        """POST /api/campaigns without auth returns 401"""
        fresh_session = requests.Session()
        fresh_session.headers.update({"Content-Type": "application/json"})
        response = fresh_session.post(f"{BASE_URL}/api/campaigns", json={
            "name": "Test",
            "channel": "whatsapp",
            "message": "Hello",
            "recipients": ["+1234567890"]
        })
        assert response.status_code == 401, f"Expected 401 without auth, got {response.status_code}"


class TestCampaignsValidation:
    """Campaign creation validation tests"""
    
    def test_create_campaign_missing_message(self, auth_session):
        """POST /api/campaigns with missing message returns 400"""
        response = auth_session.post(f"{BASE_URL}/api/campaigns", json={
            "name": "Test Campaign",
            "channel": "whatsapp",
            "message": "",  # Empty message
            "recipients": ["+1234567890"]
        })
        assert response.status_code == 400, f"Expected 400 for missing message, got {response.status_code}"
        assert "message" in response.text.lower() or "requis" in response.text.lower()
    
    def test_create_campaign_empty_recipients(self, auth_session):
        """POST /api/campaigns with empty recipients returns 400"""
        response = auth_session.post(f"{BASE_URL}/api/campaigns", json={
            "name": "Test Campaign",
            "channel": "whatsapp",
            "message": "Hello {name}!",
            "recipients": []  # Empty recipients
        })
        assert response.status_code == 400, f"Expected 400 for empty recipients, got {response.status_code}"
        assert "destinataire" in response.text.lower() or "recipient" in response.text.lower()
    
    def test_create_campaign_invalid_channel(self, auth_session):
        """POST /api/campaigns with invalid channel returns 400"""
        response = auth_session.post(f"{BASE_URL}/api/campaigns", json={
            "name": "Test Campaign",
            "channel": "email",  # Invalid channel (should be whatsapp or sms)
            "message": "Hello!",
            "recipients": ["+1234567890"]
        })
        assert response.status_code == 400, f"Expected 400 for invalid channel, got {response.status_code}"
        assert "channel" in response.text.lower() or "whatsapp" in response.text.lower() or "sms" in response.text.lower()


class TestCampaignsCRUD:
    """Campaign CRUD operations"""
    
    @pytest.fixture(scope="class")
    def created_campaign_id(self, auth_session):
        """Create a test campaign and return its ID"""
        response = auth_session.post(f"{BASE_URL}/api/campaigns", json={
            "name": "TEST_Iteration11_Campaign",
            "channel": "whatsapp",
            "message": "Bonjour {name}, ceci est un test de Switia!",
            "recipients": [
                {"phone": "+14155551234", "name": "Test User 1"},
                {"phone": "+14155555678", "name": "Test User 2"},
                "+14155559999"  # String format also supported
            ]
        })
        assert response.status_code == 200, f"Failed to create campaign: {response.text}"
        data = response.json()
        assert "id" in data, "Response should contain campaign id"
        assert "campaign" in data, "Response should contain campaign object"
        print(f"Created test campaign: {data['id']}")
        return data["id"]
    
    def test_create_campaign_success(self, auth_session, created_campaign_id):
        """POST /api/campaigns creates campaign with recipients"""
        # Campaign already created in fixture, verify it exists
        response = auth_session.get(f"{BASE_URL}/api/campaigns/{created_campaign_id}")
        assert response.status_code == 200
        data = response.json()
        
        # Validate campaign structure
        assert data.get("name") == "TEST_Iteration11_Campaign"
        assert data.get("channel") == "whatsapp"
        assert "message_template" in data
        assert "recipients" in data
        assert len(data["recipients"]) == 3
        assert data.get("status") == "draft"
        
        # Validate recipients have required fields
        for r in data["recipients"]:
            assert "phone" in r
            assert "status" in r
            assert r["status"] == "pending"
    
    def test_list_campaigns(self, auth_session, created_campaign_id):
        """GET /api/campaigns lists user's campaigns sorted desc"""
        response = auth_session.get(f"{BASE_URL}/api/campaigns")
        assert response.status_code == 200
        data = response.json()
        
        assert "campaigns" in data
        campaigns = data["campaigns"]
        assert isinstance(campaigns, list)
        
        # Our test campaign should be in the list
        campaign_ids = [c.get("_id") for c in campaigns]
        assert created_campaign_id in campaign_ids, "Created campaign should be in list"
        
        # Campaigns should not include full recipients array in list view
        for c in campaigns:
            assert "recipients" not in c or c.get("recipients") is None, "List view should not include recipients"
        
        print(f"Listed {len(campaigns)} campaigns")
    
    def test_get_campaign_by_id(self, auth_session, created_campaign_id):
        """GET /api/campaigns/{id} returns full campaign with recipients"""
        response = auth_session.get(f"{BASE_URL}/api/campaigns/{created_campaign_id}")
        assert response.status_code == 200
        data = response.json()
        
        # Full campaign should include recipients
        assert "recipients" in data
        assert len(data["recipients"]) == 3
        assert data.get("total") == 3
        assert data.get("sent") == 0
        assert data.get("failed") == 0
    
    def test_get_campaign_not_found(self, auth_session):
        """GET /api/campaigns/{invalid_id} returns 404"""
        response = auth_session.get(f"{BASE_URL}/api/campaigns/000000000000000000000000")
        assert response.status_code == 404
    
    def test_get_campaign_invalid_id(self, auth_session):
        """GET /api/campaigns/{invalid_format} returns 400"""
        response = auth_session.get(f"{BASE_URL}/api/campaigns/invalid-id-format")
        assert response.status_code == 400


class TestCampaignSend:
    """Campaign send functionality (Twilio sandbox)"""
    
    @pytest.fixture(scope="class")
    def send_test_campaign_id(self, auth_session):
        """Create a campaign specifically for send testing"""
        response = auth_session.post(f"{BASE_URL}/api/campaigns", json={
            "name": "TEST_Send_Campaign",
            "channel": "whatsapp",
            "message": "Test message from Switia",
            "recipients": [
                {"phone": "+14155551111", "name": "Send Test"}
            ]
        })
        assert response.status_code == 200
        return response.json()["id"]
    
    def test_send_campaign_graceful_failure(self, auth_session, send_test_campaign_id):
        """POST /api/campaigns/{id}/send handles Twilio sandbox failures gracefully"""
        response = auth_session.post(f"{BASE_URL}/api/campaigns/{send_test_campaign_id}/send")
        
        # Should NOT return 500 - should handle gracefully
        assert response.status_code != 500, f"Send should not 500, got: {response.text}"
        
        # Should return 200 with status info (even if sends fail)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert "ok" in data
        assert "sent" in data
        assert "failed" in data
        assert "status" in data
        
        # Status should be 'sent', 'partial', or 'failed' (not 'draft' or 'sending')
        assert data["status"] in ["sent", "partial", "failed"], f"Unexpected status: {data['status']}"
        
        print(f"Send result: sent={data['sent']}, failed={data['failed']}, status={data['status']}")
        
        # Verify campaign was updated
        get_resp = auth_session.get(f"{BASE_URL}/api/campaigns/{send_test_campaign_id}")
        assert get_resp.status_code == 200
        campaign = get_resp.json()
        assert campaign["status"] in ["sent", "partial", "failed"]
        
        # If failed, recipients should have error info
        if data["failed"] > 0:
            for r in campaign.get("recipients", []):
                if r.get("status") == "failed":
                    assert r.get("error") is not None, "Failed recipient should have error message"
    
    def test_send_campaign_already_sent_or_retry(self, auth_session, send_test_campaign_id):
        """POST /api/campaigns/{id}/send on already sent/failed campaign - behavior depends on status"""
        # First check the campaign status
        get_resp = auth_session.get(f"{BASE_URL}/api/campaigns/{send_test_campaign_id}")
        assert get_resp.status_code == 200
        campaign = get_resp.json()
        current_status = campaign.get("status")
        
        # Try to send again
        response = auth_session.post(f"{BASE_URL}/api/campaigns/{send_test_campaign_id}/send")
        
        if current_status == "sent":
            # If fully sent, should reject with 400
            assert response.status_code == 400, f"Expected 400 for already sent, got {response.status_code}"
            assert "déjà" in response.text.lower() or "already" in response.text.lower()
        else:
            # If failed/partial, allows retry (returns 200 or 400 depending on implementation)
            # The code allows retry for failed campaigns, which is correct behavior
            assert response.status_code in [200, 400], f"Unexpected status: {response.status_code}"
            print(f"Retry on {current_status} campaign: status={response.status_code}")


class TestCampaignSMSNoFrom:
    """Test SMS campaign when TWILIO_SMS_FROM is not configured"""
    
    def test_sms_campaign_send_without_from_number(self, auth_session):
        """POST /api/campaigns/{id}/send for SMS without TWILIO_SMS_FROM returns 400"""
        # Create SMS campaign
        create_resp = auth_session.post(f"{BASE_URL}/api/campaigns", json={
            "name": "TEST_SMS_No_From",
            "channel": "sms",
            "message": "SMS test",
            "recipients": ["+14155551234"]
        })
        assert create_resp.status_code == 200
        campaign_id = create_resp.json()["id"]
        
        # Try to send - should fail gracefully if TWILIO_SMS_FROM is not set
        send_resp = auth_session.post(f"{BASE_URL}/api/campaigns/{campaign_id}/send")
        
        # If TWILIO_SMS_FROM is not configured, should return 400 with clear message
        # If it IS configured, it will try to send (and likely fail in sandbox)
        if send_resp.status_code == 400:
            assert "sms" in send_resp.text.lower() or "twilio" in send_resp.text.lower()
            print("SMS send correctly rejected: TWILIO_SMS_FROM not configured")
        else:
            # If it tried to send, should still not 500
            assert send_resp.status_code != 500
            print(f"SMS send attempted: {send_resp.json()}")
        
        # Cleanup
        auth_session.delete(f"{BASE_URL}/api/campaigns/{campaign_id}")


class TestCampaignDelete:
    """Campaign deletion tests"""
    
    def test_delete_campaign(self, auth_session):
        """DELETE /api/campaigns/{id} removes campaign"""
        # Create a campaign to delete
        create_resp = auth_session.post(f"{BASE_URL}/api/campaigns", json={
            "name": "TEST_Delete_Campaign",
            "channel": "whatsapp",
            "message": "To be deleted",
            "recipients": ["+14155551234"]
        })
        assert create_resp.status_code == 200
        campaign_id = create_resp.json()["id"]
        
        # Delete it
        delete_resp = auth_session.delete(f"{BASE_URL}/api/campaigns/{campaign_id}")
        assert delete_resp.status_code == 200
        data = delete_resp.json()
        assert data.get("ok") == True
        
        # Verify it's gone
        get_resp = auth_session.get(f"{BASE_URL}/api/campaigns/{campaign_id}")
        assert get_resp.status_code == 404
        print(f"Campaign {campaign_id} deleted successfully")
    
    def test_delete_campaign_not_found(self, auth_session):
        """DELETE /api/campaigns/{invalid_id} returns 404"""
        response = auth_session.delete(f"{BASE_URL}/api/campaigns/000000000000000000000000")
        assert response.status_code == 404


class TestCleanup:
    """Cleanup test data"""
    
    def test_cleanup_test_campaigns(self, auth_session):
        """Remove all TEST_ prefixed campaigns"""
        response = auth_session.get(f"{BASE_URL}/api/campaigns")
        if response.status_code == 200:
            campaigns = response.json().get("campaigns", [])
            deleted = 0
            for c in campaigns:
                if c.get("name", "").startswith("TEST_"):
                    del_resp = auth_session.delete(f"{BASE_URL}/api/campaigns/{c['_id']}")
                    if del_resp.status_code == 200:
                        deleted += 1
            print(f"Cleaned up {deleted} test campaigns")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
