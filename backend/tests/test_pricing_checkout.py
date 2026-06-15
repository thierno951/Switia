"""
Test suite for Agovia Pricing, Checkout, and Transaction APIs
Tests the Stripe integration fix and new transaction history feature
"""
import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

class TestAuth:
    """Authentication tests - login with admin credentials"""
    
    @pytest.fixture(scope="class")
    def session(self):
        """Create a requests session for cookie-based auth"""
        return requests.Session()
    
    def test_admin_login(self, session):
        """Test login with admin@agovia.com / admin123"""
        response = session.post(f"{BASE_URL}/api/auth/login", json={
            "email": "admin@agovia.com",
            "password": "admin123"
        })
        assert response.status_code == 200, f"Login failed: {response.text}"
        data = response.json()
        assert data["email"] == "admin@agovia.com"
        assert data["role"] == "admin"
        print(f"✓ Admin login successful: {data['email']}")


class TestPlansAPI:
    """Test GET /api/plans - returns 3 subscription plans"""
    
    def test_get_plans_returns_three_plans(self):
        """Verify /api/plans returns free, pro, enterprise plans"""
        response = requests.get(f"{BASE_URL}/api/plans")
        assert response.status_code == 200, f"Plans API failed: {response.text}"
        
        plans = response.json()
        assert len(plans) == 3, f"Expected 3 plans, got {len(plans)}"
        
        plan_ids = [p["id"] for p in plans]
        assert "free" in plan_ids, "Missing 'free' plan"
        assert "pro" in plan_ids, "Missing 'pro' plan"
        assert "enterprise" in plan_ids, "Missing 'enterprise' plan"
        
        # Verify plan structure
        for plan in plans:
            assert "id" in plan
            assert "name" in plan
            assert "price" in plan
            assert "currency" in plan
            assert "features" in plan
            assert isinstance(plan["features"], list)
        
        print(f"✓ GET /api/plans returns 3 plans: {plan_ids}")


class TestSubscriptionAPI:
    """Test GET /api/subscription - returns user's current subscription with usage stats"""
    
    @pytest.fixture(scope="class")
    def auth_session(self):
        """Create authenticated session"""
        session = requests.Session()
        response = session.post(f"{BASE_URL}/api/auth/login", json={
            "email": "admin@agovia.com",
            "password": "admin123"
        })
        assert response.status_code == 200, "Login failed for subscription test"
        return session
    
    def test_get_subscription_returns_usage_stats(self, auth_session):
        """Verify /api/subscription returns plan info and usage stats"""
        response = auth_session.get(f"{BASE_URL}/api/subscription")
        assert response.status_code == 200, f"Subscription API failed: {response.text}"
        
        data = response.json()
        
        # Verify required fields
        assert "plan_id" in data, "Missing plan_id"
        assert "plan_name" in data, "Missing plan_name"
        assert "conversations_used" in data, "Missing conversations_used"
        assert "conversations_limit" in data, "Missing conversations_limit"
        assert "analyses_used" in data, "Missing analyses_used"
        assert "analyses_limit" in data, "Missing analyses_limit"
        
        # Verify data types
        assert isinstance(data["conversations_used"], int)
        assert isinstance(data["analyses_used"], int)
        
        print(f"✓ GET /api/subscription returns: plan={data['plan_name']}, "
              f"conversations={data['conversations_used']}/{data['conversations_limit']}, "
              f"analyses={data['analyses_used']}/{data['analyses_limit']}")


class TestCheckoutAPI:
    """Test POST /api/checkout - creates Stripe checkout session"""
    
    @pytest.fixture(scope="class")
    def auth_session(self):
        """Create authenticated session"""
        session = requests.Session()
        response = session.post(f"{BASE_URL}/api/auth/login", json={
            "email": "admin@agovia.com",
            "password": "admin123"
        })
        assert response.status_code == 200, "Login failed for checkout test"
        return session
    
    def test_create_checkout_session_for_pro_plan(self, auth_session):
        """Verify POST /api/checkout creates session and returns checkout_url + session_id"""
        response = auth_session.post(f"{BASE_URL}/api/checkout", json={
            "plan_id": "pro",
            "origin_url": "https://intelligent-ops-14.preview.emergentagent.com"
        })
        assert response.status_code == 200, f"Checkout API failed: {response.text}"
        
        data = response.json()
        
        # Verify required fields
        assert "checkout_url" in data, "Missing checkout_url"
        assert "session_id" in data, "Missing session_id"
        
        # Verify checkout_url is a valid Stripe URL
        assert data["checkout_url"].startswith("https://checkout.stripe.com"), \
            f"Invalid checkout URL: {data['checkout_url']}"
        
        # Verify session_id format
        assert data["session_id"].startswith("cs_test_"), \
            f"Invalid session_id format: {data['session_id']}"
        
        print(f"✓ POST /api/checkout created session: {data['session_id'][:30]}...")
        return data["session_id"]
    
    def test_checkout_rejects_free_plan(self, auth_session):
        """Verify checkout rejects free plan"""
        response = auth_session.post(f"{BASE_URL}/api/checkout", json={
            "plan_id": "free",
            "origin_url": "https://intelligent-ops-14.preview.emergentagent.com"
        })
        assert response.status_code == 400, "Should reject free plan checkout"
        print("✓ POST /api/checkout correctly rejects free plan")
    
    def test_checkout_rejects_invalid_plan(self, auth_session):
        """Verify checkout rejects invalid plan"""
        response = auth_session.post(f"{BASE_URL}/api/checkout", json={
            "plan_id": "invalid_plan",
            "origin_url": "https://intelligent-ops-14.preview.emergentagent.com"
        })
        assert response.status_code == 400, "Should reject invalid plan"
        print("✓ POST /api/checkout correctly rejects invalid plan")


class TestCheckoutStatusAPI:
    """Test GET /api/checkout/status/{session_id} - THE MAIN BUG FIX TEST"""
    
    @pytest.fixture(scope="class")
    def auth_session(self):
        """Create authenticated session"""
        session = requests.Session()
        response = session.post(f"{BASE_URL}/api/auth/login", json={
            "email": "admin@agovia.com",
            "password": "admin123"
        })
        assert response.status_code == 200, "Login failed for checkout status test"
        return session
    
    def test_checkout_status_returns_valid_response_not_500(self, auth_session):
        """
        CRITICAL TEST: Verify /api/checkout/status/{session_id} returns valid status, NOT 500 error
        This was the main bug: emergentintegrations library's get_checkout_status() had a Pydantic 
        validation bug (StripeObject not serializable). Fixed by using stripe library directly.
        """
        import time
        
        # First create a checkout session
        checkout_response = auth_session.post(f"{BASE_URL}/api/checkout", json={
            "plan_id": "pro",
            "origin_url": "https://intelligent-ops-14.preview.emergentagent.com"
        })
        assert checkout_response.status_code == 200, "Failed to create checkout session"
        session_id = checkout_response.json()["session_id"]
        
        # Wait for Stripe session to propagate (Emergent proxy timing)
        time.sleep(2)
        
        # Now test the status endpoint - THIS SHOULD NOT RETURN 500
        status_response = auth_session.get(f"{BASE_URL}/api/checkout/status/{session_id}")
        
        # The main assertion - should NOT be 500
        assert status_response.status_code != 500, \
            f"BUG NOT FIXED: /api/checkout/status returned 500 error: {status_response.text}"
        
        assert status_response.status_code == 200, \
            f"Checkout status failed with {status_response.status_code}: {status_response.text}"
        
        data = status_response.json()
        
        # Verify response structure
        assert "status" in data, "Missing 'status' field"
        assert "payment_status" in data, "Missing 'payment_status' field"
        assert "plan_id" in data, "Missing 'plan_id' field"
        
        # For a new session, status should be 'open'/'pending' and payment_status should be 'unpaid'/'pending'
        assert data["status"] in ["open", "complete", "expired", "pending"], \
            f"Unexpected status: {data['status']}"
        assert data["payment_status"] in ["paid", "unpaid", "pending", "no_payment_required"], \
            f"Unexpected payment_status: {data['payment_status']}"
        
        print(f"✓ GET /api/checkout/status/{session_id[:20]}... returned: "
              f"status={data['status']}, payment_status={data['payment_status']}")
    
    def test_checkout_status_returns_404_for_nonexistent_session(self, auth_session):
        """Verify 404 for non-existent session"""
        response = auth_session.get(f"{BASE_URL}/api/checkout/status/cs_test_nonexistent123")
        assert response.status_code == 404, \
            f"Expected 404 for non-existent session, got {response.status_code}"
        print("✓ GET /api/checkout/status returns 404 for non-existent session")


class TestTransactionsAPI:
    """Test GET /api/transactions - returns list of user's payment transactions"""
    
    @pytest.fixture(scope="class")
    def auth_session(self):
        """Create authenticated session"""
        session = requests.Session()
        response = session.post(f"{BASE_URL}/api/auth/login", json={
            "email": "admin@agovia.com",
            "password": "admin123"
        })
        assert response.status_code == 200, "Login failed for transactions test"
        return session
    
    def test_get_transactions_returns_list(self, auth_session):
        """Verify /api/transactions returns transaction list"""
        response = auth_session.get(f"{BASE_URL}/api/transactions")
        assert response.status_code == 200, f"Transactions API failed: {response.text}"
        
        data = response.json()
        assert "transactions" in data, "Missing 'transactions' field"
        assert isinstance(data["transactions"], list), "transactions should be a list"
        
        # If there are transactions, verify structure
        if len(data["transactions"]) > 0:
            txn = data["transactions"][0]
            assert "session_id" in txn, "Missing session_id in transaction"
            assert "plan_id" in txn, "Missing plan_id in transaction"
            assert "plan_name" in txn, "Missing plan_name in transaction"
            assert "amount" in txn, "Missing amount in transaction"
            assert "currency" in txn, "Missing currency in transaction"
            assert "payment_status" in txn, "Missing payment_status in transaction"
            assert "created_at" in txn, "Missing created_at in transaction"
            
            print(f"✓ GET /api/transactions returned {len(data['transactions'])} transactions")
            print(f"  Sample: plan={txn['plan_name']}, amount={txn['amount']}{txn['currency']}, "
                  f"status={txn['payment_status']}")
        else:
            print("✓ GET /api/transactions returned empty list (no transactions yet)")
    
    def test_transactions_requires_auth(self):
        """Verify /api/transactions requires authentication"""
        response = requests.get(f"{BASE_URL}/api/transactions")
        assert response.status_code == 401, "Transactions should require auth"
        print("✓ GET /api/transactions correctly requires authentication")


class TestEndToEndCheckoutFlow:
    """End-to-end test of the checkout flow"""
    
    @pytest.fixture(scope="class")
    def auth_session(self):
        """Create authenticated session"""
        session = requests.Session()
        response = session.post(f"{BASE_URL}/api/auth/login", json={
            "email": "admin@agovia.com",
            "password": "admin123"
        })
        assert response.status_code == 200, "Login failed"
        return session
    
    def test_full_checkout_flow(self, auth_session):
        """Test complete flow: plans -> subscription -> checkout -> status -> transactions"""
        
        # Step 1: Get plans
        plans_response = auth_session.get(f"{BASE_URL}/api/plans")
        assert plans_response.status_code == 200
        plans = plans_response.json()
        print(f"Step 1: Got {len(plans)} plans")
        
        # Step 2: Get current subscription
        sub_response = auth_session.get(f"{BASE_URL}/api/subscription")
        assert sub_response.status_code == 200
        subscription = sub_response.json()
        print(f"Step 2: Current plan is '{subscription['plan_name']}'")
        
        # Step 3: Create checkout for enterprise plan
        checkout_response = auth_session.post(f"{BASE_URL}/api/checkout", json={
            "plan_id": "enterprise",
            "origin_url": "https://intelligent-ops-14.preview.emergentagent.com"
        })
        assert checkout_response.status_code == 200
        checkout_data = checkout_response.json()
        session_id = checkout_data["session_id"]
        print(f"Step 3: Created checkout session {session_id[:25]}...")
        
        # Step 4: Check status (THE CRITICAL FIX)
        status_response = auth_session.get(f"{BASE_URL}/api/checkout/status/{session_id}")
        assert status_response.status_code == 200, \
            f"CHECKOUT STATUS FAILED: {status_response.status_code} - {status_response.text}"
        status_data = status_response.json()
        print(f"Step 4: Checkout status = {status_data['status']}, payment = {status_data['payment_status']}")
        
        # Step 5: Get transactions
        txn_response = auth_session.get(f"{BASE_URL}/api/transactions")
        assert txn_response.status_code == 200
        transactions = txn_response.json()["transactions"]
        print(f"Step 5: Got {len(transactions)} transactions")
        
        # Verify the new transaction exists
        session_ids = [t["session_id"] for t in transactions]
        assert session_id in session_ids, "New checkout session not found in transactions"
        print("✓ Full checkout flow completed successfully!")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
