from http.server import BaseHTTPRequestHandler, HTTPServer
import json

class MockCRMHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.send_header('Set-Cookie', 'sessionid=12345')
        self.end_headers()
        
        if '/checkserver.cfm' in self.path:
            self.wfile.write(b"<html><title>Dealer Portal</title><body>Login Success</body></html>")
        elif '/adddiaryentry.cfm' in self.path:
            self.wfile.write(b"<html><body>Success! Diary entry moved.</body></html>")
        elif '/followup3' in self.path:
            self.wfile.write(b"<html><body>Success! Diary entry moved.</body></html>")
        else:
            self.wfile.write(b"<html><body>OK</body></html>")

    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        
        if '/entries.cfm' in self.path:
            html = """
            <html>
                <input id="sg" value="mocksg123">
                <form action="adddiaryentry.cfm">
                    <input name="custid" value="999">
                    <input name="contactname" value="Test Customer">
                    <input name="purpose" value="Follow up">
                </form>
            </html>
            """
            self.wfile.write(html.encode('utf-8'))
        elif '/adddiaryentry.cfm' in self.path:
            html = """
            <html>
                <span>Customer : Test Customer</span>
                <input id="nextvehicleid" value="101">
                <form name="form1" action="/followup3.cfm">
                </form>
            </html>
            """
            self.wfile.write(html.encode('utf-8'))
        else:
            self.wfile.write(b"<html><body>OK</body></html>")

server = HTTPServer(('127.0.0.1', 8080), MockCRMHandler)
print("Mock CRM running on port 8080")
server.serve_forever()
