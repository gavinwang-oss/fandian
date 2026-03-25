# Week 1 Setup (Local)

Goal: get a local SMS echo bot running so that when a guest texts your Twilio number, your computer replies automatically.

## Day 1: Install deps + SMS echo

### 1) Create a Python virtual environment
Command:
```bash
python3 -m venv .venv
```
Why:
- This creates a clean, isolated Python environment for this project.
- It prevents package conflicts with other projects on your machine.

### 2) Activate the virtual environment
Command:
```bash
source .venv/bin/activate
```
Why:
- This tells your terminal to use the Python inside `.venv`.
- You should see your prompt change to include `(.venv)`.

### 3) Install dependencies
Command:
```bash
pip install flask python-dotenv twilio
```
Why:
- `flask`: small web server to receive Twilio webhooks.
- `python-dotenv`: load secrets from a `.env` file.
- `twilio`: helper library for Twilio SMS replies (we will use TwiML).

### 4) Create a `.env` file for secrets
Create a file named `.env` in your project folder, with:
```
TWILIO_SID=your_account_sid
TWILIO_TOKEN=your_auth_token
TWILIO_NUMBER=+1xxxxxxxxxx
```
Why:
- Keeps secrets out of your code.
- Makes it easy to change credentials without editing source.

### 5) Minimal Flask webhook (echo)
Create `app.py` with:
```python
from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse

app = Flask(__name__)

@app.route("/sms", methods=["POST"])
def sms_reply():
    body = request.values.get("Body", "")
    resp = MessagingResponse()
    resp.message(f"You said: {body}")
    return str(resp)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
```
Why:
- Twilio calls `/sms` when a message arrives.
- We reply directly with TwiML (no extra API call needed).

### 6) Run the server
Command:
```bash
python app.py
```
Why:
- Starts your local server on port 5000.

### 7) Expose with ngrok
Command:
```bash
ngrok http 5000
```
Why:
- Your local machine is not public. ngrok gives you a public URL.
- Copy the `https://...ngrok.io` URL.

### 8) Configure Twilio webhook
In Twilio Console:
- Phone Numbers -> your number
- Messaging -> A MESSAGE COMES IN
- Set webhook to: `https://<your-ngrok-id>.ngrok.io/sms`

### 9) Test
- Text your Twilio number from your phone.
- You should get an echo reply.

## Troubleshooting
- If you see 404s, your webhook URL is wrong (make sure it ends with `/sms`).
- If no reply, check the ngrok window and your Flask logs.
- If port 5000 is in use, run `python app.py` on another port and update ngrok.
