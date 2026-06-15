"""
Test suite for Iteration 5 features:
1. Profile management (PUT /api/profile) - update name, email, password
2. Team management (POST/GET/DELETE /api/team/*) - invite, list, remove members
3. Enhanced dashboard (GET /api/dashboard/stats?period=) - date filtering, new metrics
"""

import pytest
import requests
import os
import secrets

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

# Test credentials from environment
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@switia.com")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")


class TestAuth:
    """Authentication tests"""
    
    def test_login_admin(self):
        """Test admin login with correct credentials"""
        response = requests.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
        )
        assert response.status_code == 200, f"Login failed: {response.text}"
        data = response.json()
        assert data["email"] == ADMIN_EMAIL
        assert data["role"] == "admin"
        print(f"✓ Admin login successful: {data['name']}")


class TestProfile:
    """Profile management tests (PUT /api/profile)"""
    
    @pytest.fixture
    def auth_session(self):
        """Get authenticated session"""
        session = requests.Session()
        response = session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
        )
        assert response.status_code == 200
        return session
    
    def test_update_name(self, auth_session):
        """Test updating user name"""
        # Get current user info
        me_response = auth_session.get(f"{BASE_URL}/api/auth/me")
        assert me_response.status_code == 200
        original_name = me_response.json()["name"]
        
        # Update name
        new_name = f"TEST_Admin_{secrets.token_hex(4)}"
        response = auth_session.put(
            f"{BASE_URL}/api/profile",
            json={"name": new_name}
        )
        assert response.status_code == 200, f"Update failed: {response.text}"
        data = response.json()
        assert data["name"] == new_name
        print(f"✓ Name updated to: {new_name}")
        
        # Restore original name
        auth_session.put(f"{BASE_URL}/api/profile", json={"name": original_name})
    
    def test_password_change_requires_current_password(self, auth_session):
        """Test that password change requires current_password"""
        response = auth_session.put(
            f"{BASE_URL}/api/profile",
            json={"new_password": "newpassword123"}
        )
        assert response.status_code == 400, f"Expected 400, got {response.status_code}"
        assert "actuel" in response.json()["detail"].lower() or "current" in response.json()["detail"].lower()
        print("✓ Password change correctly requires current_password")
    
    def test_wrong_current_password_returns_400(self, auth_session):
        """Test that wrong current password returns 400"""
        response = auth_session.put(
            f"{BASE_URL}/api/profile",
            json={
                "current_password": "wrongpassword",
                "new_password": "newpassword123"
            }
        )
        assert response.status_code == 400, f"Expected 400, got {response.status_code}"
        assert "incorrect" in response.json()["detail"].lower()
        print("✓ Wrong current password correctly returns 400")
    
    def test_no_changes_returns_400(self, auth_session):
        """Test that empty update returns 400"""
        response = auth_session.put(
            f"{BASE_URL}/api/profile",
            json={}
        )
        assert response.status_code == 400
        print("✓ Empty update correctly returns 400")


class TestTeamManagement:
    """Team management tests (POST/GET/DELETE /api/team/*)"""
    
    @pytest.fixture
    def admin_session(self):
        """Get admin authenticated session"""
        session = requests.Session()
        response = session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
        )
        assert response.status_code == 200
        return session
    
    def test_invite_team_member(self, admin_session):
        """Test inviting a new team member"""
        test_email = f"test_member_{secrets.token_hex(4)}@example.com"
        test_name = f"TEST_Member_{secrets.token_hex(4)}"
        
        response = admin_session.post(
            f"{BASE_URL}/api/team/invite",
            json={
                "email": test_email,
                "name": test_name,
                "role": "member"
            }
        )
        assert response.status_code == 200, f"Invite failed: {response.text}"
        data = response.json()
        assert data["email"] == test_email.lower()
        assert data["name"] == test_name
        assert data["role"] == "member"
        assert "temp_password" in data
        assert len(data["temp_password"]) > 0
        print(f"✓ Team member invited: {test_email} with temp password")
        
        # Store member_id for cleanup
        return data["id"]
    
    def test_list_team_members(self, admin_session):
        """Test listing team members"""
        response = admin_session.get(f"{BASE_URL}/api/team/members")
        assert response.status_code == 200, f"List failed: {response.text}"
        data = response.json()
        assert "members" in data
        assert isinstance(data["members"], list)
        
        # Admin should be in the list
        admin_found = any(m["email"] == ADMIN_EMAIL for m in data["members"])
        assert admin_found, "Admin not found in team members list"
        print(f"✓ Team members listed: {len(data['members'])} members")
    
    def test_remove_team_member(self, admin_session):
        """Test removing a team member"""
        # First invite a member
        test_email = f"test_remove_{secrets.token_hex(4)}@example.com"
        invite_response = admin_session.post(
            f"{BASE_URL}/api/team/invite",
            json={
                "email": test_email,
                "name": "TEST_ToRemove",
                "role": "member"
            }
        )
        assert invite_response.status_code == 200
        member_id = invite_response.json()["id"]
        
        # Remove the member
        delete_response = admin_session.delete(f"{BASE_URL}/api/team/members/{member_id}")
        assert delete_response.status_code == 200, f"Delete failed: {delete_response.text}"
        print(f"✓ Team member removed: {member_id}")
        
        # Verify member is gone
        list_response = admin_session.get(f"{BASE_URL}/api/team/members")
        members = list_response.json()["members"]
        assert not any(m["id"] == member_id for m in members), "Member still in list after deletion"
        print("✓ Verified member no longer in list")
    
    def test_non_admin_cannot_invite(self, admin_session):
        """Test that non-admin users get 403 on team endpoints"""
        # Create a member account
        test_email = f"test_nonadmin_{secrets.token_hex(4)}@example.com"
        invite_response = admin_session.post(
            f"{BASE_URL}/api/team/invite",
            json={
                "email": test_email,
                "name": "TEST_NonAdmin",
                "role": "member"
            }
        )
        assert invite_response.status_code == 200
        temp_password = invite_response.json()["temp_password"]
        member_id = invite_response.json()["id"]
        
        # Login as the member
        member_session = requests.Session()
        login_response = member_session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": test_email, "password": temp_password}
        )
        assert login_response.status_code == 200, f"Member login failed: {login_response.text}"
        
        # Try to invite as member - should get 403
        invite_as_member = member_session.post(
            f"{BASE_URL}/api/team/invite",
            json={
                "email": "another@example.com",
                "name": "Another",
                "role": "member"
            }
        )
        assert invite_as_member.status_code == 403, f"Expected 403, got {invite_as_member.status_code}"
        print("✓ Non-admin correctly gets 403 on invite")
        
        # Try to list members as member - should get 403
        list_as_member = member_session.get(f"{BASE_URL}/api/team/members")
        assert list_as_member.status_code == 403, f"Expected 403, got {list_as_member.status_code}"
        print("✓ Non-admin correctly gets 403 on list members")
        
        # Cleanup - remove the test member
        admin_session.delete(f"{BASE_URL}/api/team/members/{member_id}")


class TestEnhancedDashboard:
    """Enhanced dashboard tests (GET /api/dashboard/stats?period=)"""
    
    @pytest.fixture
    def auth_session(self):
        """Get authenticated session"""
        session = requests.Session()
        response = session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
        )
        assert response.status_code == 200
        return session
    
    def test_dashboard_stats_7d(self, auth_session):
        """Test dashboard stats with 7d period filter"""
        response = auth_session.get(f"{BASE_URL}/api/dashboard/stats?period=7d")
        assert response.status_code == 200, f"Stats failed: {response.text}"
        data = response.json()
        
        # Check required fields
        assert "total_conversations" in data
        assert "total_tickets" in data
        assert "total_analyses" in data
        assert data["period"] == "7d"
        print(f"✓ Dashboard stats (7d): conversations={data['total_conversations']}, tickets={data['total_tickets']}")
    
    def test_dashboard_stats_30d_enhanced_fields(self, auth_session):
        """Test dashboard stats with 30d period returns enhanced fields"""
        response = auth_session.get(f"{BASE_URL}/api/dashboard/stats?period=30d")
        assert response.status_code == 200, f"Stats failed: {response.text}"
        data = response.json()
        
        # Check enhanced fields
        assert "resolved_tickets" in data, "Missing resolved_tickets field"
        assert "escalation_rate" in data, "Missing escalation_rate field"
        assert "widget_sessions" in data, "Missing widget_sessions field"
        assert "activity_timeline" in data, "Missing activity_timeline field"
        
        # Validate types
        assert isinstance(data["resolved_tickets"], int)
        assert isinstance(data["escalation_rate"], (int, float))
        assert isinstance(data["widget_sessions"], int)
        assert isinstance(data["activity_timeline"], list)
        
        print("✓ Dashboard stats (30d) with enhanced fields:")
        print(f"  - resolved_tickets: {data['resolved_tickets']}")
        print(f"  - escalation_rate: {data['escalation_rate']}%")
        print(f"  - widget_sessions: {data['widget_sessions']}")
        print(f"  - activity_timeline: {len(data['activity_timeline'])} data points")
    
    def test_dashboard_stats_90d(self, auth_session):
        """Test dashboard stats with 90d period filter"""
        response = auth_session.get(f"{BASE_URL}/api/dashboard/stats?period=90d")
        assert response.status_code == 200
        data = response.json()
        assert data["period"] == "90d"
        print("✓ Dashboard stats (90d) works correctly")
    
    def test_dashboard_stats_all(self, auth_session):
        """Test dashboard stats with 'all' period (no filter)"""
        response = auth_session.get(f"{BASE_URL}/api/dashboard/stats?period=all")
        assert response.status_code == 200
        data = response.json()
        assert data["period"] == "all"
        print("✓ Dashboard stats (all) works correctly")
    
    def test_activity_timeline_format(self, auth_session):
        """Test that activity_timeline has correct format"""
        response = auth_session.get(f"{BASE_URL}/api/dashboard/stats?period=30d")
        assert response.status_code == 200
        data = response.json()
        
        timeline = data.get("activity_timeline", [])
        if len(timeline) > 0:
            # Check first item has correct structure
            item = timeline[0]
            assert "date" in item, "Timeline item missing 'date' field"
            assert "count" in item, "Timeline item missing 'count' field"
            print(f"✓ Activity timeline format correct: {timeline[0]}")
        else:
            print("✓ Activity timeline is empty (no activity data)")


class TestCleanup:
    """Cleanup test data"""
    
    def test_cleanup_test_members(self):
        """Remove any TEST_ prefixed members"""
        session = requests.Session()
        session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
        )
        
        response = session.get(f"{BASE_URL}/api/team/members")
        if response.status_code == 200:
            members = response.json().get("members", [])
            for member in members:
                if member.get("name", "").startswith("TEST_") or member.get("email", "").startswith("test_"):
                    session.delete(f"{BASE_URL}/api/team/members/{member['id']}")
                    print(f"  Cleaned up: {member['email']}")
        print("✓ Cleanup complete")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
