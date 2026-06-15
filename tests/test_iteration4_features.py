"""
Test suite for Iteration 4 features:
1. Ticket status management (PATCH /api/support/tickets/{ticket_id})
2. More chart types (pie, scatter) for Analysis Agent
3. Export PDF endpoint for analysis reports
4. Serve real embed.js for the widget
"""

import pytest
import requests
import os
import io

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

class TestAuth:
    """Authentication tests"""
    
    @pytest.fixture(scope="class")
    def session(self):
        """Create authenticated session"""
        s = requests.Session()
        s.headers.update({"Content-Type": "application/json"})
        return s
    
    @pytest.fixture(scope="class")
    def auth_cookies(self, session):
        """Login and get auth cookies"""
        response = session.post(f"{BASE_URL}/api/auth/login", json={
            "email": "admin@agovia.com",
            "password": "admin123"
        })
        assert response.status_code == 200, f"Login failed: {response.text}"
        data = response.json()
        assert data["email"] == "admin@agovia.com"
        assert data["role"] == "admin"
        return session.cookies
    
    def test_login_success(self, session, auth_cookies):
        """Test login with admin credentials"""
        # Already tested in fixture, just verify cookies exist
        assert auth_cookies is not None
        print("PASS: Login with admin@agovia.com / admin123")


class TestTicketStatusManagement:
    """Test ticket status PATCH endpoint"""
    
    @pytest.fixture(scope="class")
    def session(self):
        s = requests.Session()
        s.headers.update({"Content-Type": "application/json"})
        # Login
        response = s.post(f"{BASE_URL}/api/auth/login", json={
            "email": "admin@agovia.com",
            "password": "admin123"
        })
        assert response.status_code == 200
        return s
    
    @pytest.fixture(scope="class")
    def test_ticket(self, session):
        """Create a test ticket for status updates"""
        response = session.post(f"{BASE_URL}/api/support/ticket", json={
            "subject": "TEST_Status_Update_Ticket",
            "description": "Test ticket for status management testing",
            "priority": "medium"
        })
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "open"
        return data
    
    def test_update_ticket_to_in_progress(self, session, test_ticket):
        """Test PATCH ticket status to in_progress"""
        ticket_id = test_ticket["id"]
        response = session.patch(f"{BASE_URL}/api/support/tickets/{ticket_id}", json={
            "status": "in_progress"
        })
        assert response.status_code == 200, f"Failed: {response.text}"
        data = response.json()
        assert data["status"] == "in_progress"
        print("PASS: PATCH /api/support/tickets/{ticket_id} with status 'in_progress'")
    
    def test_update_ticket_to_resolved(self, session, test_ticket):
        """Test PATCH ticket status to resolved"""
        ticket_id = test_ticket["id"]
        response = session.patch(f"{BASE_URL}/api/support/tickets/{ticket_id}", json={
            "status": "resolved"
        })
        assert response.status_code == 200, f"Failed: {response.text}"
        data = response.json()
        assert data["status"] == "resolved"
        print("PASS: PATCH /api/support/tickets/{ticket_id} with status 'resolved'")
    
    def test_update_ticket_to_closed(self, session, test_ticket):
        """Test PATCH ticket status to closed"""
        ticket_id = test_ticket["id"]
        response = session.patch(f"{BASE_URL}/api/support/tickets/{ticket_id}", json={
            "status": "closed"
        })
        assert response.status_code == 200, f"Failed: {response.text}"
        data = response.json()
        assert data["status"] == "closed"
        print("PASS: PATCH /api/support/tickets/{ticket_id} with status 'closed'")
    
    def test_update_ticket_invalid_status(self, session, test_ticket):
        """Test PATCH ticket with invalid status returns 400"""
        ticket_id = test_ticket["id"]
        response = session.patch(f"{BASE_URL}/api/support/tickets/{ticket_id}", json={
            "status": "invalid_status"
        })
        assert response.status_code == 400, f"Expected 400, got {response.status_code}"
        data = response.json()
        assert "Invalid status" in data.get("detail", "")
        print("PASS: PATCH /api/support/tickets/{ticket_id} with invalid status returns 400")
    
    def test_get_tickets_shows_updated_status(self, session):
        """Verify GET tickets shows updated status"""
        response = session.get(f"{BASE_URL}/api/support/tickets")
        assert response.status_code == 200
        tickets = response.json()
        # Find our test ticket
        test_tickets = [t for t in tickets if t["subject"] == "TEST_Status_Update_Ticket"]
        assert len(test_tickets) > 0, "Test ticket not found"
        assert test_tickets[0]["status"] == "closed"
        print("PASS: GET /api/support/tickets shows updated status")


class TestAnalysisChartTypes:
    """Test analysis upload returns pie and scatter charts"""
    
    @pytest.fixture(scope="class")
    def session(self):
        s = requests.Session()
        # Login
        response = s.post(f"{BASE_URL}/api/auth/login", json={
            "email": "admin@agovia.com",
            "password": "admin123"
        })
        assert response.status_code == 200
        return s
    
    @pytest.fixture(scope="class")
    def csv_content(self):
        """Create CSV with categorical and numeric columns for all chart types"""
        return """category,region,sales,profit,quantity
Electronics,North,1500,300,10
Electronics,South,2000,400,15
Clothing,North,800,150,20
Clothing,South,1200,250,25
Food,North,500,100,30
Food,South,600,120,35
Electronics,East,1800,350,12
Clothing,East,900,180,22
Food,East,550,110,32
Electronics,West,2200,450,18
Clothing,West,1100,220,28
Food,West,650,130,38"""
    
    @pytest.fixture(scope="class")
    def upload_result(self, session, csv_content):
        """Upload CSV and get analysis result"""
        files = {
            'file': ('test_data.csv', io.BytesIO(csv_content.encode()), 'text/csv')
        }
        response = session.post(f"{BASE_URL}/api/analysis/upload", files=files)
        assert response.status_code == 200, f"Upload failed: {response.text}"
        return response.json()
    
    def test_upload_returns_charts(self, upload_result):
        """Test upload returns charts array"""
        assert "charts" in upload_result
        assert len(upload_result["charts"]) > 0
        print(f"PASS: Upload returns {len(upload_result['charts'])} charts")
    
    def test_upload_returns_pie_chart(self, upload_result):
        """Test upload returns pie chart for categorical data"""
        charts = upload_result["charts"]
        pie_charts = [c for c in charts if c["type"] == "pie"]
        assert len(pie_charts) > 0, f"No pie chart found. Chart types: {[c['type'] for c in charts]}"
        
        pie_chart = pie_charts[0]
        assert "data" in pie_chart
        assert len(pie_chart["data"]) > 0
        # Verify pie chart data structure
        assert "name" in pie_chart["data"][0]
        assert "value" in pie_chart["data"][0]
        print(f"PASS: Upload returns pie chart with {len(pie_chart['data'])} segments")
    
    def test_upload_returns_scatter_chart(self, upload_result):
        """Test upload returns scatter chart for numeric columns"""
        charts = upload_result["charts"]
        scatter_charts = [c for c in charts if c["type"] == "scatter"]
        assert len(scatter_charts) > 0, f"No scatter chart found. Chart types: {[c['type'] for c in charts]}"
        
        scatter_chart = scatter_charts[0]
        assert "data" in scatter_chart
        assert len(scatter_chart["data"]) > 0
        # Verify scatter chart data structure
        assert "x" in scatter_chart["data"][0]
        assert "y" in scatter_chart["data"][0]
        # Verify axis keys
        assert "xKey" in scatter_chart or "title" in scatter_chart
        print(f"PASS: Upload returns scatter chart with {len(scatter_chart['data'])} points")
    
    def test_upload_returns_bar_chart(self, upload_result):
        """Test upload returns bar chart"""
        charts = upload_result["charts"]
        bar_charts = [c for c in charts if c["type"] == "bar"]
        assert len(bar_charts) > 0, f"No bar chart found. Chart types: {[c['type'] for c in charts]}"
        print(f"PASS: Upload returns {len(bar_charts)} bar chart(s)")
    
    def test_upload_returns_line_chart(self, upload_result):
        """Test upload returns line/area chart"""
        charts = upload_result["charts"]
        line_charts = [c for c in charts if c["type"] == "line"]
        assert len(line_charts) > 0, f"No line chart found. Chart types: {[c['type'] for c in charts]}"
        print(f"PASS: Upload returns {len(line_charts)} line chart(s)")


class TestAnalysisExport:
    """Test analysis export endpoint"""
    
    @pytest.fixture(scope="class")
    def session(self):
        s = requests.Session()
        response = s.post(f"{BASE_URL}/api/auth/login", json={
            "email": "admin@agovia.com",
            "password": "admin123"
        })
        assert response.status_code == 200
        return s
    
    @pytest.fixture(scope="class")
    def session_id(self, session):
        """Upload CSV and get session_id"""
        csv_content = """name,age,salary
John,30,50000
Jane,25,45000
Bob,35,60000"""
        files = {
            'file': ('export_test.csv', io.BytesIO(csv_content.encode()), 'text/csv')
        }
        response = session.post(f"{BASE_URL}/api/analysis/upload", files=files)
        assert response.status_code == 200
        return response.json()["session_id"]
    
    def test_export_endpoint_returns_data(self, session, session_id):
        """Test GET /api/analysis/export/{session_id} returns exportable data"""
        response = session.get(f"{BASE_URL}/api/analysis/export/{session_id}")
        assert response.status_code == 200, f"Export failed: {response.text}"
        data = response.json()
        
        # Verify required fields
        assert "filename" in data
        assert "summary" in data
        assert "sample_data" in data
        assert "exported_at" in data
        
        # Verify summary structure
        assert "rows" in data["summary"]
        assert "columns" in data["summary"]
        
        # Verify sample_data is a list
        assert isinstance(data["sample_data"], list)
        assert len(data["sample_data"]) > 0
        
        print("PASS: GET /api/analysis/export/{session_id} returns exportable data with summary and sample_data")
    
    def test_export_invalid_session_returns_404(self, session):
        """Test export with invalid session returns 404"""
        response = session.get(f"{BASE_URL}/api/analysis/export/invalid-session-id")
        assert response.status_code == 404
        print("PASS: Export with invalid session returns 404")


class TestWidgetEmbedJS:
    """Test widget embed.js endpoint"""
    
    def test_embed_js_returns_javascript(self):
        """Test GET /api/widget/embed.js returns JavaScript content"""
        response = requests.get(f"{BASE_URL}/api/widget/embed.js")
        assert response.status_code == 200, f"Failed: {response.status_code}"
        
        # Check content-type
        content_type = response.headers.get("Content-Type", "")
        assert "javascript" in content_type.lower(), f"Wrong content-type: {content_type}"
        
        print("PASS: GET /api/widget/embed.js returns JavaScript with correct content-type")
    
    def test_embed_js_contains_widget_code(self):
        """Test embed.js contains working widget code"""
        response = requests.get(f"{BASE_URL}/api/widget/embed.js")
        content = response.text
        
        # Verify key widget elements
        assert "agovia-widget-btn" in content, "Missing widget button"
        assert "agovia-widget-panel" in content, "Missing widget panel"
        assert "X-Widget-Key" in content, "Missing API key header"
        assert "/api/widget/chat" in content, "Missing chat endpoint"
        assert "Agovia" in content, "Missing Agovia branding"
        
        print("PASS: embed.js contains working chat widget with Agovia branding and API integration")
    
    def test_embed_js_has_cors_header(self):
        """Test embed.js has CORS header for cross-origin embedding"""
        response = requests.get(f"{BASE_URL}/api/widget/embed.js")
        cors_header = response.headers.get("Access-Control-Allow-Origin", "")
        assert cors_header == "*", f"Missing or wrong CORS header: {cors_header}"
        print("PASS: embed.js has Access-Control-Allow-Origin: * header")


class TestTicketLabelsInFrench:
    """Verify ticket status labels are in French (backend returns status codes, frontend displays French)"""
    
    @pytest.fixture(scope="class")
    def session(self):
        s = requests.Session()
        response = s.post(f"{BASE_URL}/api/auth/login", json={
            "email": "admin@agovia.com",
            "password": "admin123"
        })
        assert response.status_code == 200
        return s
    
    def test_ticket_statuses_are_valid(self, session):
        """Test that valid statuses are: open, in_progress, resolved, closed"""
        # Create a ticket
        response = session.post(f"{BASE_URL}/api/support/ticket", json={
            "subject": "TEST_French_Labels_Ticket",
            "description": "Testing French labels",
            "priority": "low"
        })
        assert response.status_code == 200
        ticket = response.json()
        ticket_id = ticket["id"]
        
        # Test all valid statuses
        valid_statuses = ["open", "in_progress", "resolved", "closed"]
        for status in valid_statuses:
            response = session.patch(f"{BASE_URL}/api/support/tickets/{ticket_id}", json={
                "status": status
            })
            assert response.status_code == 200, f"Status '{status}' failed"
        
        print("PASS: All ticket statuses (open, in_progress, resolved, closed) are valid")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
