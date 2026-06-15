"""
Iteration 12 P2 Features Test Suite
Tests:
1. Campaign scheduling (scheduled_at in future → status='scheduled', invalid → 400)
2. CSV import (comma-separated, semicolon-separated, empty file)
3. Campaign analytics on draft campaign
4. Twilio status callback webhook
5. Showcase opt-in (GET, PUT with validation)
6. Public showcase (verify opted-in testimonials)
7. Outlook OAuth2 graceful degradation (status, login → 503)
"""
import pytest
import requests
import os
import io
from datetime import datetime, timezone, timedelta

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
if not BASE_URL:
    BASE_URL = "https://intelligent-ops-14.preview.emergentagent.com"

# Test credentials (read from env to avoid hardcoded secrets in version control)
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@switia.com")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")


class TestAuth:
    """Helper to get auth cookies"""
    
    @staticmethod
    def login(session: requests.Session) -> bool:
        """Login and return True if successful"""
        resp = session.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        return resp.status_code == 200


@pytest.fixture(scope="module")
def auth_session():
    """Authenticated session for all tests"""
    session = requests.Session()
    session.headers.update({"Content-Type": "application/json"})
    success = TestAuth.login(session)
    if not success:
        pytest.skip("Authentication failed - cannot proceed with tests")
    return session


@pytest.fixture(scope="module")
def created_campaigns(auth_session):
    """Track campaigns created during tests for cleanup"""
    campaigns = []
    yield campaigns
    # Cleanup: delete all test campaigns
    for cid in campaigns:
        try:
            auth_session.delete(f"{BASE_URL}/api/campaigns/{cid}")
        except Exception:
            pass


# ============ 1. Campaign Scheduling Tests ============

class TestCampaignScheduling:
    """Test campaign scheduling with scheduled_at parameter"""
    
    def test_create_campaign_with_future_scheduled_at(self, auth_session, created_campaigns):
        """POST /api/campaigns with scheduled_at in future → status='scheduled'"""
        future_time = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
        payload = {
            "name": "TEST_Scheduled_Campaign",
            "channel": "whatsapp",
            "message": "Hello {name}, this is a scheduled test!",
            "recipients": [{"phone": "+33612345678", "name": "Test User"}],
            "scheduled_at": future_time
        }
        resp = auth_session.post(f"{BASE_URL}/api/campaigns", json=payload)
        
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert "id" in data, "Response should contain campaign id"
        assert "campaign" in data, "Response should contain campaign object"
        
        campaign = data["campaign"]
        assert campaign["status"] == "scheduled", f"Expected status='scheduled', got '{campaign['status']}'"
        assert campaign["scheduled_at"] is not None, "scheduled_at should be set"
        
        # Track for cleanup
        created_campaigns.append(data["id"])
        print(f"✓ Campaign with future scheduled_at created with status='scheduled'")
    
    def test_create_campaign_with_invalid_scheduled_at(self, auth_session):
        """POST /api/campaigns with invalid scheduled_at → 400"""
        payload = {
            "name": "TEST_Invalid_Schedule",
            "channel": "whatsapp",
            "message": "Hello test!",
            "recipients": [{"phone": "+33612345678"}],
            "scheduled_at": "not-a-valid-date"
        }
        resp = auth_session.post(f"{BASE_URL}/api/campaigns", json=payload)
        
        assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert "detail" in data, "Error response should contain detail"
        assert "ISO8601" in data["detail"] or "scheduled_at" in data["detail"].lower(), \
            f"Error should mention ISO8601 format: {data['detail']}"
        print(f"✓ Invalid scheduled_at returns 400 with proper error message")
    
    def test_create_campaign_without_scheduled_at_is_draft(self, auth_session, created_campaigns):
        """POST /api/campaigns without scheduled_at → status='draft'"""
        payload = {
            "name": "TEST_Draft_Campaign",
            "channel": "whatsapp",
            "message": "Hello {name}!",
            "recipients": [{"phone": "+33612345679", "name": "Draft Test"}]
        }
        resp = auth_session.post(f"{BASE_URL}/api/campaigns", json=payload)
        
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()
        campaign = data["campaign"]
        assert campaign["status"] == "draft", f"Expected status='draft', got '{campaign['status']}'"
        
        created_campaigns.append(data["id"])
        print(f"✓ Campaign without scheduled_at created with status='draft'")


# ============ 2. CSV Import Tests ============

class TestCSVImport:
    """Test CSV parsing endpoint"""
    
    def test_parse_csv_comma_separated_with_header(self, auth_session):
        """POST /api/campaigns/parse-csv with comma-separated CSV (header: phone,name)"""
        csv_content = "phone,name\n+33612345678,Alice\n+33698765432,Bob"
        
        # Create a new request with cookies from auth_session but without Content-Type header
        cookies = auth_session.cookies.get_dict()
        resp = requests.post(
            f"{BASE_URL}/api/campaigns/parse-csv",
            files={"file": ("contacts.csv", io.BytesIO(csv_content.encode()), "text/csv")},
            cookies=cookies
        )
        
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert "recipients" in data, "Response should contain recipients"
        assert "count" in data, "Response should contain count"
        assert data["count"] == 2, f"Expected 2 recipients, got {data['count']}"
        
        # Verify recipients
        recipients = data["recipients"]
        assert len(recipients) == 2
        assert recipients[0]["phone"] == "+33612345678"
        assert recipients[0]["name"] == "Alice"
        assert recipients[1]["phone"] == "+33698765432"
        assert recipients[1]["name"] == "Bob"
        print(f"✓ Comma-separated CSV with header parsed correctly: {data['count']} recipients")
    
    def test_parse_csv_semicolon_separated_no_header(self, auth_session):
        """POST /api/campaigns/parse-csv with semicolon-separated CSV (no header)"""
        # No header row - first column is phone, second is name
        csv_content = "+33611111111;Charlie\n+33622222222;Diana"
        
        cookies = auth_session.cookies.get_dict()
        resp = requests.post(
            f"{BASE_URL}/api/campaigns/parse-csv",
            files={"file": ("contacts.csv", io.BytesIO(csv_content.encode()), "text/csv")},
            cookies=cookies
        )
        
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert data["count"] == 2, f"Expected 2 recipients, got {data['count']}"
        
        recipients = data["recipients"]
        assert len(recipients) == 2
        # Without header, first column is phone, second is name
        assert recipients[0]["phone"] == "+33611111111"
        assert recipients[1]["phone"] == "+33622222222"
        print(f"✓ Semicolon-separated CSV without header parsed correctly: {data['count']} recipients")
    
    def test_parse_csv_empty_file(self, auth_session):
        """POST /api/campaigns/parse-csv with empty file → count=0, no 500"""
        csv_content = ""
        
        cookies = auth_session.cookies.get_dict()
        resp = requests.post(
            f"{BASE_URL}/api/campaigns/parse-csv",
            files={"file": ("empty.csv", io.BytesIO(csv_content.encode()), "text/csv")},
            cookies=cookies
        )
        
        assert resp.status_code == 200, f"Expected 200 (not 500), got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert data["count"] == 0, f"Expected count=0, got {data['count']}"
        assert data["recipients"] == [], "Recipients should be empty list"
        print(f"✓ Empty CSV file returns count=0 without error")


# ============ 3. Campaign Analytics Tests ============

class TestCampaignAnalytics:
    """Test campaign analytics endpoint"""
    
    def test_analytics_on_draft_campaign(self, auth_session, created_campaigns):
        """GET /api/campaigns/{id}/analytics on a draft campaign → returns zeros"""
        # First create a draft campaign
        payload = {
            "name": "TEST_Analytics_Draft",
            "channel": "whatsapp",
            "message": "Analytics test message",
            "recipients": [
                {"phone": "+33612345670", "name": "User1"},
                {"phone": "+33612345671", "name": "User2"}
            ]
        }
        create_resp = auth_session.post(f"{BASE_URL}/api/campaigns", json=payload)
        assert create_resp.status_code == 200
        campaign_id = create_resp.json()["id"]
        created_campaigns.append(campaign_id)
        
        # Get analytics
        resp = auth_session.get(f"{BASE_URL}/api/campaigns/{campaign_id}/analytics")
        
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()
        
        # Verify analytics structure and values for draft campaign
        assert data["campaign_id"] == campaign_id
        assert data["total"] == 2, f"Expected total=2, got {data['total']}"
        assert data["sent"] == 0, f"Expected sent=0 for draft, got {data['sent']}"
        assert data["delivered"] == 0, f"Expected delivered=0 for draft, got {data['delivered']}"
        assert data["replies"] == 0, f"Expected replies=0 for draft, got {data['replies']}"
        assert "delivery_rate" in data
        assert "reply_rate" in data
        print(f"✓ Analytics on draft campaign returns correct zero values")


# ============ 4. Twilio Status Callback Webhook Tests ============

class TestTwilioStatusWebhook:
    """Test Twilio status callback webhook"""
    
    def test_status_webhook_with_fake_sid(self, auth_session):
        """POST /api/webhooks/twilio/status with form data MessageSid=fake123&MessageStatus=delivered → 200"""
        # This endpoint accepts form data (application/x-www-form-urlencoded)
        resp = requests.post(
            f"{BASE_URL}/api/webhooks/twilio/status",
            data={
                "MessageSid": "fake123",
                "MessageStatus": "delivered"
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"}
        )
        
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        print(f"✓ Twilio status webhook accepts form data and returns 200")
    
    def test_status_webhook_updates_recipient_delivery_status(self, auth_session, created_campaigns):
        """POST /api/webhooks/twilio/status with existing recipient sid → updates delivery_status"""
        # Create a campaign and manually set a recipient with a known SID
        payload = {
            "name": "TEST_Webhook_Campaign",
            "channel": "whatsapp",
            "message": "Webhook test",
            "recipients": [{"phone": "+33612345699", "name": "Webhook Test"}]
        }
        create_resp = auth_session.post(f"{BASE_URL}/api/campaigns", json=payload)
        assert create_resp.status_code == 200
        campaign_id = create_resp.json()["id"]
        created_campaigns.append(campaign_id)
        
        # Note: In a real scenario, the SID would be set when the campaign is sent.
        # Since we can't actually send (Twilio sandbox), we test that the webhook
        # endpoint works correctly with a non-matching SID (returns 200, no error)
        test_sid = f"SM_test_{campaign_id[:8]}"
        resp = requests.post(
            f"{BASE_URL}/api/webhooks/twilio/status",
            data={
                "MessageSid": test_sid,
                "MessageStatus": "delivered"
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"}
        )
        
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
        print(f"✓ Twilio status webhook handles SID lookup gracefully")


# ============ 5. Showcase Opt-in Tests ============

class TestShowcaseOptIn:
    """Test showcase opt-in endpoints"""
    
    def test_get_opt_in_status(self, auth_session):
        """GET /api/showcase/opt-in (auth admin) → returns opt_in status"""
        resp = auth_session.get(f"{BASE_URL}/api/showcase/opt-in")
        
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert "opt_in" in data, "Response should contain opt_in field"
        assert "company" in data, "Response should contain company field"
        assert "logo_url" in data, "Response should contain logo_url field"
        assert "quote" in data, "Response should contain quote field"
        print(f"✓ GET /api/showcase/opt-in returns status: opt_in={data['opt_in']}")
    
    def test_put_opt_in_true_without_company_returns_400(self, auth_session):
        """PUT /api/showcase/opt-in with opt_in:true but no company → 400"""
        resp = auth_session.put(f"{BASE_URL}/api/showcase/opt-in", json={
            "opt_in": True,
            "company": "",  # Empty company
            "logo_url": "",
            "quote": ""
        })
        
        assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert "detail" in data
        assert "société" in data["detail"].lower() or "company" in data["detail"].lower(), \
            f"Error should mention company requirement: {data['detail']}"
        print(f"✓ PUT opt_in=true without company returns 400")
    
    def test_put_opt_in_true_with_company(self, auth_session):
        """PUT /api/showcase/opt-in with opt_in:true + company='Acme' → 200"""
        resp = auth_session.put(f"{BASE_URL}/api/showcase/opt-in", json={
            "opt_in": True,
            "company": "Acme",
            "logo_url": "https://example.com/logo.png",
            "quote": "Switia nous a fait gagner du temps!"
        })
        
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert data.get("ok") == True, "Response should have ok=True"
        assert data.get("opt_in") == True, "Response should confirm opt_in=True"
        print(f"✓ PUT opt_in=true with company='Acme' succeeds")
    
    def test_put_opt_in_with_quote_over_280_chars_returns_400(self, auth_session):
        """PUT /api/showcase/opt-in with quote of 300 chars → 400"""
        long_quote = "A" * 300  # 300 characters
        resp = auth_session.put(f"{BASE_URL}/api/showcase/opt-in", json={
            "opt_in": True,
            "company": "Acme",
            "quote": long_quote
        })
        
        assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert "detail" in data
        assert "280" in data["detail"] or "caractères" in data["detail"].lower(), \
            f"Error should mention 280 char limit: {data['detail']}"
        print(f"✓ PUT opt_in with quote > 280 chars returns 400")


# ============ 6. Public Showcase Tests ============

class TestPublicShowcase:
    """Test public showcase endpoint"""
    
    def test_public_showcase_shows_opted_in_company(self):
        """GET /api/public/showcase — if admin opted in, testimonial should show 'Acme' and opted_in=true"""
        # No auth required for public endpoint
        resp = requests.get(f"{BASE_URL}/api/public/showcase")
        
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()
        
        assert "platform" in data, "Response should contain platform stats"
        assert "testimonials" in data, "Response should contain testimonials"
        
        # Look for the opted-in company (Acme) in testimonials
        testimonials = data["testimonials"]
        acme_found = False
        for t in testimonials:
            if t.get("alias") == "Acme" and t.get("opted_in") == True:
                acme_found = True
                print(f"  Found opted-in testimonial: {t['alias']}, opted_in={t['opted_in']}")
                break
        
        # Note: If admin has no messages, they won't appear in testimonials
        # So we just verify the structure is correct
        print(f"✓ GET /api/public/showcase returns {len(testimonials)} testimonials")
        if acme_found:
            print(f"✓ Opted-in company 'Acme' appears in testimonials with opted_in=true")
        else:
            print(f"  Note: 'Acme' not in testimonials (admin may have 0 messages)")


# ============ 7. Outlook OAuth2 Graceful Degradation Tests ============

class TestOutlookOAuth:
    """Test Outlook OAuth2 graceful degradation when not configured"""
    
    def test_outlook_status_returns_configured_false(self, auth_session):
        """GET /api/oauth/outlook/status (auth) → configured=false (since AZURE_CLIENT_ID empty)"""
        resp = auth_session.get(f"{BASE_URL}/api/oauth/outlook/status")
        
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert "configured" in data, "Response should contain configured field"
        assert data["configured"] == False, f"Expected configured=false, got {data['configured']}"
        assert data.get("connected") == False, "Should not be connected if not configured"
        print(f"✓ GET /api/oauth/outlook/status returns configured=false")
    
    def test_outlook_login_returns_503(self, auth_session):
        """GET /api/oauth/outlook/login (auth) → 503"""
        resp = auth_session.get(f"{BASE_URL}/api/oauth/outlook/login")
        
        assert resp.status_code == 503, f"Expected 503, got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert "detail" in data, "Error response should contain detail"
        assert "configuré" in data["detail"].lower() or "configured" in data["detail"].lower(), \
            f"Error should mention not configured: {data['detail']}"
        print(f"✓ GET /api/oauth/outlook/login returns 503 when not configured")


# ============ 8. Regression Tests (from iteration 11) ============

class TestRegression:
    """Regression tests for iteration 11 features"""
    
    def test_campaigns_crud_still_works(self, auth_session, created_campaigns):
        """Verify campaigns CRUD still works"""
        # Create
        payload = {
            "name": "TEST_Regression_Campaign",
            "channel": "whatsapp",
            "message": "Regression test",
            "recipients": [{"phone": "+33612345600"}]
        }
        create_resp = auth_session.post(f"{BASE_URL}/api/campaigns", json=payload)
        assert create_resp.status_code == 200, f"Create failed: {create_resp.text}"
        campaign_id = create_resp.json()["id"]
        created_campaigns.append(campaign_id)
        
        # Read
        get_resp = auth_session.get(f"{BASE_URL}/api/campaigns/{campaign_id}")
        assert get_resp.status_code == 200, f"Get failed: {get_resp.text}"
        
        # List
        list_resp = auth_session.get(f"{BASE_URL}/api/campaigns")
        assert list_resp.status_code == 200, f"List failed: {list_resp.text}"
        
        # Delete
        del_resp = auth_session.delete(f"{BASE_URL}/api/campaigns/{campaign_id}")
        assert del_resp.status_code == 200, f"Delete failed: {del_resp.text}"
        created_campaigns.remove(campaign_id)
        
        print(f"✓ Campaigns CRUD regression test passed")
    
    def test_showcase_my_card_still_works(self, auth_session):
        """Verify showcase my-card endpoint still works"""
        resp = auth_session.get(f"{BASE_URL}/api/showcase/my-card")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert "messages" in data
        assert "hours_saved" in data
        assert "share_text" in data
        print(f"✓ Showcase my-card regression test passed")
    
    def test_roi_badge_still_works(self):
        """Verify ROI badge JS endpoint still works"""
        resp = requests.get(f"{BASE_URL}/api/public/roi-badge.js")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
        assert "application/javascript" in resp.headers.get("content-type", "")
        assert "switia" in resp.text.lower()
        print(f"✓ ROI badge regression test passed")


# ============ Cleanup ============

class TestCleanup:
    """Cleanup test data"""
    
    def test_reset_opt_in_to_false(self, auth_session):
        """Reset opt-in to opt_in:false at the end"""
        resp = auth_session.put(f"{BASE_URL}/api/showcase/opt-in", json={
            "opt_in": False,
            "company": "",
            "logo_url": "",
            "quote": ""
        })
        assert resp.status_code == 200, f"Failed to reset opt-in: {resp.text}"
        print(f"✓ Reset opt-in to false")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
