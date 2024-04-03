from flask import Flask, request, abort
import openai
import os
import re
from dotenv import load_dotenv
from sqlalchemy import create_engine, Column, Integer, String, DateTime
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime, timedelta
import prompt

load_dotenv()

api_key = os.getenv("OPENAI_API_KEY")
ACCESS_TOKEN = os.getenv("LINE_ACCESS_TOKEN")
CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")

# SQLAlchemy configuration
DATABASE_URL = "sqlite:///messages.db"
engine = create_engine(DATABASE_URL)
Base = declarative_base()

# Define the Message model
class Message(Base):
    __tablename__ = 'messages'
    id = Column(Integer, primary_key=True)
    user_id = Column(String(255))
    timestamp = Column(DateTime)
    user_text = Column(String(255))
    reply_text = Column(String(255))

Base.metadata.create_all(bind=engine)

# Create a session to interact with the database
Session = sessionmaker(bind=engine)
session = Session()

from linebot.v3 import (
    WebhookHandler
)
from linebot.v3.exceptions import (
    InvalidSignatureError
)
from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi,
    ReplyMessageRequest,
    PushMessageRequest,
    TextMessage
)
from linebot.v3.webhooks import (
    MessageEvent,
    TextMessageContent
)

app = Flask(__name__)

configuration = Configuration(access_token=ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)


@app.route("/")
def test():
    return "OK"


@app.route("/callback", methods=['POST'])
def callback():
    # get X-Line-Signature header value
    signature = request.headers['X-Line-Signature']

    # get request body as text
    body = request.get_data(as_text=True)
    app.logger.info("Request body: " + body)

    # handle webhook body
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        app.logger.info("Invalid signature. Please check your channel access token/channel secret.")
        abort(400)

    return 'OK'


system_prompt = prompt.system_prompt


@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    userId = event.source.user_id
    timestamp = datetime.utcfromtimestamp(event.timestamp / 1000.0)  # Convert timestamp to datetime
    prompt = event.message.text

    print(prompt)

    messages_for_gpt = []

    if user_id_exists(userId):
        message_list = get_messages_by_user_id(userId)

        for message_tuple in message_list:
            user_text = message_tuple[0]
            reply_text = message_tuple[1]

            user_text_gpt = {"role": "user", "content": user_text}
            reply_text_gpt = {"role": "assistant", "content": reply_text}

            messages_for_gpt.append(user_text_gpt)
            messages_for_gpt.append(reply_text_gpt)

    
    messages_for_gpt.append({"role": "system", "content": system_prompt})
    messages_for_gpt.append({"role": "user", "content": prompt})
    
    client = openai.OpenAI(api_key=api_key)
    gpt_model = "gpt-4-1106-preview"
    response = client.chat.completions.create(
                        model = gpt_model,
                        messages = messages_for_gpt,
                        temperature=0,
                    )
    
    reply_message = response.choices[0].message.content
    print(reply_message)
    # reply_message = "テスト"

    # 受信したメッセージをデータベースに保存
    save_message(userId, timestamp, prompt, reply_message)

    # send message
    reply_message_list = split_string_and_newline(reply_message)
    send_message_list = [TextMessage(text=reply_message_item) for reply_message_item in reply_message_list]

    if len(send_message_list) > 5:
        send_message_list = [TextMessage(text=reply_message)]

    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message_with_http_info(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=send_message_list
            )
        )

def user_id_exists(user_id):
    # Check if user_id exists in the messages table
    existing_user = session.query(Message).filter_by(user_id=user_id).first()
    return existing_user is not None

def save_message(user_id, timestamp, user_text, reply_text):
    # Save the message to the database
    message = Message(
        user_id=user_id,
        timestamp=timestamp,
        user_text=user_text,
        reply_text=reply_text
    )
    session.add(message)
    session.commit()

def get_messages_by_user_id(user_id):
    # Calculate 1 day ago from now
    one_day_ago = datetime.utcnow() - timedelta(days=1)

    # SQLAlchemy query to get user_text and reply_text for a specific user_id within the last 1 day, ordered by timestamp
    messages = session.query(Message.user_text, Message.reply_text).filter(
        Message.user_id == user_id,
        Message.timestamp >= one_day_ago
    ).order_by(Message.timestamp).all()

    return messages

def split_string_and_newline(input_string):
    # 句点とハテナマークで改行する
    newlined_text = re.sub(r'([。？])', r'\1\n', input_string)
    
    # 改行で文字列を分割してリストにする
    result_list = newlined_text.split('\n')
    
    # 空白文字や空の文字列を取り除く
    result_list = [s.strip() for s in result_list if s.strip()]

    # 各要素から最後の句点を取り除く
    result_list = [s[:-1] if s.endswith('。') else s for s in result_list]
    
    return result_list

if __name__ == "__main__":
    app.run()