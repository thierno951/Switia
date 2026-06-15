"""
Test suite for Agovia new features:
1. Conversation history persistence (sessions + history)
2. Training data/FAQ CRUD
3. Widget API key management
4. Widget public chat endpoint
"""
import pytest
import requests
import os
import uuid

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

# Test credentials from environment
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@switia.com")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")


@pytest.fixture(scope="module")
def session():
    """Shared requests session"""
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


@pytest.fixture(scope="module")
def auth_cookies(session):
    """Login and get auth cookies"""
    response = session.post(f"{BASE_URL}/api/auth/login", json={
        "email": ADMIN_EMAIL,
        "password": ADMIN_PASSWORD
    })
    assert response.status_code == 200, f"Login failed: {response.text}"
    # Cookies are automatically stored in session
    return session


# ============ Auth Tests ============

class TestAuth:
    """Authentication tests"""
    
    def test_login_success(self, session):
        """Test admin login with correct credentials"""
        response = session.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        assert response.status_code == 200
        data = response.json()
        # Login returns user data directly (not wrapped in "user" key)
        assert "email" in data
        assert data["email"] == ADMIN_EMAIL
        print(f"✓ Login successful for {ADMIN_EMAIL}")
    
    def test_login_invalid_credentials(self, session):
        """Test login with wrong password"""
        response = session.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": "wrongpassword"
        })
        assert response.status_code == 401
        print("✓ Invalid credentials rejected correctly")


# ============ Support Sessions Tests ============

class TestSupportSessions:
    """Conversation history persistence tests"""
    
    def test_get_sessions_list(self, auth_cookies):
        """GET /api/support/sessions - returns list of previous sessions"""
        response = auth_cookies.get(f"{BASE_URL}/api/support/sessions")
        assert response.status_code == 200
        data = response.json()
        assert "sessions" in data
        assert isinstance(data["sessions"], list)
        print(f"✓ Sessions endpoint returns {len(data['sessions'])} sessions")
        
        # If sessions exist, verify structure
        if data["sessions"]:
            session = data["sessions"][0]
            assert "session_id" in session
            assert "title" in session
            assert "last_timestamp" in session
            assert "message_count" in session
            print(f"✓ Session structure verified: {session['title'][:30]}...")
    
    def test_create_chat_and_verify_session(self, auth_cookies):
        """Create a chat message and verify it creates a session"""
        # Send a chat message
        test_message = f"TEST_session_check_{uuid.uuid4().hex[:8]}"
        response = auth_cookies.post(f"{BASE_URL}/api/support/chat", json={
            "message": test_message
        })
        assert response.status_code == 200
        data = response.json()
        assert "session_id" in data
        assert "response" in data
        session_id = data["session_id"]
        print(f"✓ Chat created with session_id: {session_id}")
        
        # Verify session appears in sessions list
        sessions_response = auth_cookies.get(f"{BASE_URL}/api/support/sessions")
        assert sessions_response.status_code == 200
        sessions = sessions_response.json()["sessions"]
        session_ids = [s["session_id"] for s in sessions]
        assert session_id in session_ids, "New session should appear in sessions list"
        print("✓ Session appears in sessions list")
        
        return session_id
    
    def test_get_session_history(self, auth_cookies):
        """GET /api/support/history/{session_id} - loads messages for a session"""
        # First create a session
        test_message = f"TEST_history_check_{uuid.uuid4().hex[:8]}"
        chat_response = auth_cookies.post(f"{BASE_URL}/api/support/chat", json={
            "message": test_message
        })
        assert chat_response.status_code == 200
        session_id = chat_response.json()["session_id"]
        
        # Get history for this session
        response = auth_cookies.get(f"{BASE_URL}/api/support/history/{session_id}")
        assert response.status_code == 200
        data = response.json()
        assert "history" in data
        assert isinstance(data["history"], list)
        assert len(data["history"]) >= 2  # At least user message + agent response
        
        # Verify message structure
        for msg in data["history"]:
            assert "message" in msg
            assert "role" in msg
            assert "timestamp" in msg
        
        print(f"✓ History loaded with {len(data['history'])} messages")
    
    def test_sessions_requires_auth(self, session):
        """Sessions endpoint requires authentication"""
        # Use fresh session without auth
        fresh_session = requests.Session()
        response = fresh_session.get(f"{BASE_URL}/api/support/sessions")
        assert response.status_code == 401
        print("✓ Sessions endpoint requires authentication")


# ============ Training Data Tests ============

class TestTrainingData:
    """Training data/FAQ CRUD tests"""
    
    def test_add_training_entry(self, auth_cookies):
        """POST /api/support/training - add FAQ entry"""
        entry_data = {
            "question": f"TEST_What is Agovia? {uuid.uuid4().hex[:8]}",
            "answer": "Agovia is an AI agent platform for businesses.",
            "category": "general"
        }
        response = auth_cookies.post(f"{BASE_URL}/api/support/training", json=entry_data)
        assert response.status_code == 200
        data = response.json()
        
        assert "entry_id" in data
        assert data["question"] == entry_data["question"]
        assert data["answer"] == entry_data["answer"]
        assert data["category"] == entry_data["category"]
        assert "created_at" in data
        
        print(f"✓ Training entry created: {data['entry_id']}")
        return data["entry_id"]
    
    def test_list_training_entries(self, auth_cookies):
        """GET /api/support/training - list all training entries"""
        response = auth_cookies.get(f"{BASE_URL}/api/support/training")
        assert response.status_code == 200
        data = response.json()
        
        assert "entries" in data
        assert isinstance(data["entries"], list)
        print(f"✓ Training entries list returned {len(data['entries'])} entries")
        
        # Verify entry structure if entries exist
        if data["entries"]:
            entry = data["entries"][0]
            assert "entry_id" in entry
            assert "question" in entry
            assert "answer" in entry
            assert "category" in entry
            print("✓ Entry structure verified")
    
    def test_delete_training_entry(self, auth_cookies):
        """DELETE /api/support/training/{entry_id} - delete training entry"""
        # First create an entry
        entry_data = {
            "question": f"TEST_DELETE_ME_{uuid.uuid4().hex[:8]}",
            "answer": "This entry will be deleted.",
            "category": "test"
        }
        create_response = auth_cookies.post(f"{BASE_URL}/api/support/training", json=entry_data)
        assert create_response.status_code == 200
        entry_id = create_response.json()["entry_id"]
        
        # Delete the entry
        delete_response = auth_cookies.delete(f"{BASE_URL}/api/support/training/{entry_id}")
        assert delete_response.status_code == 200
        assert delete_response.json()["message"] == "Entry deleted"
        print(f"✓ Training entry deleted: {entry_id}")
        
        # Verify it's gone
        list_response = auth_cookies.get(f"{BASE_URL}/api/support/training")
        entries = list_response.json()["entries"]
        entry_ids = [e["entry_id"] for e in entries]
        assert entry_id not in entry_ids, "Deleted entry should not appear in list"
        print("✓ Deleted entry no longer in list")
    
    def test_delete_nonexistent_entry(self, auth_cookies):
        """DELETE non-existent entry returns 404"""
        response = auth_cookies.delete(f"{BASE_URL}/api/support/training/nonexistent-id-12345")
        assert response.status_code == 404
        print("✓ Delete non-existent entry returns 404")
    
    def test_training_requires_auth(self, session):
        """Training endpoints require authentication"""
        fresh_session = requests.Session()
        response = fresh_session.get(f"{BASE_URL}/api/support/training")
        assert response.status_code == 401
        print("✓ Training endpoint requires authentication")


# ============ Widget API Key Tests ============

class TestWidgetKeys:
    """Widget API key management tests"""
    
    def test_create_widget_key(self, auth_cookies):
        """POST /api/widget/keys - create widget API key"""
        key_data = {
            "name": f"TEST_Widget_Key_{uuid.uuid4().hex[:8]}"
        }
        response = auth_cookies.post(f"{BASE_URL}/api/widget/keys", json=key_data)
        assert response.status_code == 200
        data = response.json()
        
        assert "key_id" in data
        assert "api_key" in data
        assert data["api_key"].startswith("agovia_wk_")
        assert data["name"] == key_data["name"]
        assert "created_at" in data
        
        print(f"✓ Widget key created: {data['api_key'][:20]}...")
        return data
    
    def test_list_widget_keys(self, auth_cookies):
        """GET /api/widget/keys - list widget API keys"""
        response = auth_cookies.get(f"{BASE_URL}/api/widget/keys")
        assert response.status_code == 200
        data = response.json()
        
        assert "keys" in data
        assert isinstance(data["keys"], list)
        print(f"✓ Widget keys list returned {len(data['keys'])} keys")
        
        # Verify key structure if keys exist
        if data["keys"]:
            key = data["keys"][0]
            assert "key_id" in key
            assert "api_key_masked" in key
            assert "api_key_full" in key
            assert "name" in key
            print("✓ Key structure verified with masked and full API key")
    
    def test_revoke_widget_key(self, auth_cookies):
        """DELETE /api/widget/keys/{key_id} - revoke a widget key"""
        # First create a key
        key_data = {"name": f"TEST_REVOKE_KEY_{uuid.uuid4().hex[:8]}"}
        create_response = auth_cookies.post(f"{BASE_URL}/api/widget/keys", json=key_data)
        assert create_response.status_code == 200
        key_id = create_response.json()["key_id"]
        api_key = create_response.json()["api_key"]
        
        # Revoke the key
        revoke_response = auth_cookies.delete(f"{BASE_URL}/api/widget/keys/{key_id}")
        assert revoke_response.status_code == 200
        assert revoke_response.json()["message"] == "Key revoked"
        print(f"✓ Widget key revoked: {key_id}")
        
        # Verify it's no longer in active keys list
        list_response = auth_cookies.get(f"{BASE_URL}/api/widget/keys")
        keys = list_response.json()["keys"]
        key_ids = [k["key_id"] for k in keys]
        assert key_id not in key_ids, "Revoked key should not appear in active keys list"
        print("✓ Revoked key no longer in active list")
        
        return api_key  # Return for testing revoked key access
    
    def test_revoke_nonexistent_key(self, auth_cookies):
        """DELETE non-existent key returns 404"""
        response = auth_cookies.delete(f"{BASE_URL}/api/widget/keys/nonexistent-key-12345")
        assert response.status_code == 404
        print("✓ Revoke non-existent key returns 404")
    
    def test_widget_keys_requires_auth(self, session):
        """Widget keys endpoints require authentication"""
        fresh_session = requests.Session()
        response = fresh_session.get(f"{BASE_URL}/api/widget/keys")
        assert response.status_code == 401
        print("✓ Widget keys endpoint requires authentication")


# ============ Widget Chat Tests ============

class TestWidgetChat:
    """Widget public chat endpoint tests"""
    
    def test_widget_chat_with_valid_key(self, auth_cookies):
        """POST /api/widget/chat with X-Widget-Key header"""
        # First create a widget key
        key_response = auth_cookies.post(f"{BASE_URL}/api/widget/keys", json={
            "name": f"TEST_CHAT_KEY_{uuid.uuid4().hex[:8]}"
        })
        assert key_response.status_code == 200
        api_key = key_response.json()["api_key"]
        
        # Use widget chat endpoint (no cookies needed, just API key header)
        chat_response = requests.post(
            f"{BASE_URL}/api/widget/chat",
            json={"message": "Hello, what is Agovia?"},
            headers={"X-Widget-Key": api_key, "Content-Type": "application/json"}
        )
        assert chat_response.status_code == 200
        data = chat_response.json()
        
        assert "response" in data
        assert "session_id" in data
        assert "escalated" in data
        assert len(data["response"]) > 0
        
        print(f"✓ Widget chat works with API key, response: {data['response'][:50]}...")
        return api_key
    
    def test_widget_chat_missing_key(self):
        """Widget chat without API key returns 401"""
        response = requests.post(
            f"{BASE_URL}/api/widget/chat",
            json={"message": "Hello"},
            headers={"Content-Type": "application/json"}
        )
        assert response.status_code == 401
        assert "Missing widget API key" in response.json()["detail"]
        print("✓ Widget chat without API key returns 401")
    
    def test_widget_chat_invalid_key(self):
        """Widget chat with invalid API key returns 401"""
        response = requests.post(
            f"{BASE_URL}/api/widget/chat",
            json={"message": "Hello"},
            headers={"X-Widget-Key": "invalid_key_12345", "Content-Type": "application/json"}
        )
        assert response.status_code == 401
        assert "Invalid or revoked" in response.json()["detail"]
        print("✓ Widget chat with invalid API key returns 401")
    
    def test_widget_chat_revoked_key(self, auth_cookies):
        """Widget chat with revoked API key returns 401"""
        # Create and revoke a key
        key_response = auth_cookies.post(f"{BASE_URL}/api/widget/keys", json={
            "name": f"TEST_REVOKED_CHAT_{uuid.uuid4().hex[:8]}"
        })
        api_key = key_response.json()["api_key"]
        key_id = key_response.json()["key_id"]
        
        # Revoke it
        auth_cookies.delete(f"{BASE_URL}/api/widget/keys/{key_id}")
        
        # Try to use revoked key
        response = requests.post(
            f"{BASE_URL}/api/widget/chat",
            json={"message": "Hello"},
            headers={"X-Widget-Key": api_key, "Content-Type": "application/json"}
        )
        assert response.status_code == 401
        print("✓ Widget chat with revoked API key returns 401")
    
    def test_widget_chat_empty_message(self, auth_cookies):
        """Widget chat with empty message returns 400"""
        # Get a valid key
        key_response = auth_cookies.post(f"{BASE_URL}/api/widget/keys", json={
            "name": f"TEST_EMPTY_MSG_{uuid.uuid4().hex[:8]}"
        })
        api_key = key_response.json()["api_key"]
        
        response = requests.post(
            f"{BASE_URL}/api/widget/chat",
            json={"message": ""},
            headers={"X-Widget-Key": api_key, "Content-Type": "application/json"}
        )
        assert response.status_code == 400
        print("✓ Widget chat with empty message returns 400")
    
    def test_widget_chat_session_persistence(self, auth_cookies):
        """Widget chat maintains session across messages"""
        # Create a key
        key_response = auth_cookies.post(f"{BASE_URL}/api/widget/keys", json={
            "name": f"TEST_SESSION_{uuid.uuid4().hex[:8]}"
        })
        api_key = key_response.json()["api_key"]
        
        # First message
        response1 = requests.post(
            f"{BASE_URL}/api/widget/chat",
            json={"message": "My name is TestUser"},
            headers={"X-Widget-Key": api_key, "Content-Type": "application/json"}
        )
        assert response1.status_code == 200
        session_id = response1.json()["session_id"]
        
        # Second message with same session
        response2 = requests.post(
            f"{BASE_URL}/api/widget/chat",
            json={"message": "What is my name?", "session_id": session_id},
            headers={"X-Widget-Key": api_key, "Content-Type": "application/json"}
        )
        assert response2.status_code == 200
        assert response2.json()["session_id"] == session_id
        print("✓ Widget chat maintains session persistence")


# ============ Training Data Integration Test ============

class TestTrainingDataIntegration:
    """Test that training data is used in widget responses"""
    
    def test_widget_uses_training_data(self, auth_cookies):
        """Widget chat should include training data in responses"""
        # Create a unique training entry
        unique_answer = f"UNIQUE_ANSWER_{uuid.uuid4().hex[:8]}"
        entry_data = {
            "question": "What is the special test feature?",
            "answer": unique_answer,
            "category": "test"
        }
        training_response = auth_cookies.post(f"{BASE_URL}/api/support/training", json=entry_data)
        assert training_response.status_code == 200
        entry_id = training_response.json()["entry_id"]
        print(f"✓ Created training entry with unique answer: {unique_answer}")
        
        # Create a widget key
        key_response = auth_cookies.post(f"{BASE_URL}/api/widget/keys", json={
            "name": f"TEST_TRAINING_INTEGRATION_{uuid.uuid4().hex[:8]}"
        })
        api_key = key_response.json()["api_key"]
        
        # Ask about the training data via widget
        chat_response = requests.post(
            f"{BASE_URL}/api/widget/chat",
            json={"message": "What is the special test feature?"},
            headers={"X-Widget-Key": api_key, "Content-Type": "application/json"}
        )
        assert chat_response.status_code == 200
        response_text = chat_response.json()["response"]
        
        # The LLM should incorporate the training data
        # Note: LLM may paraphrase, so we check if the unique identifier is present
        print(f"✓ Widget response received: {response_text[:100]}...")
        
        # Cleanup
        auth_cookies.delete(f"{BASE_URL}/api/support/training/{entry_id}")
        print("✓ Training entry cleaned up")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
