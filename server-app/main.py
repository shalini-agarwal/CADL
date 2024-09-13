from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
import json
import logging
import uvicorn
import csv
from concurrent.futures import ThreadPoolExecutor
import requests
import asyncio
from datetime import datetime
from dateutil.parser import parse
import os
import pytz
from langchain_openai import ChatOpenAI
from langchain.prompts import PromptTemplate

logging.getLogger("httpx").setLevel(logging.WARNING)
# Set up basic configuration for logging
logging.basicConfig(level=logging.DEBUG)
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows all origins
    allow_credentials=True,
    allow_methods=["*"],  # Allows all methods
    allow_headers=["*"],  # Allows all headers
)

# Initialize the GPT-4 model
llm = ChatOpenAI(openai_api_key="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", model_name="gpt-4")

executor = ThreadPoolExecutor(max_workers=10)
api_url = "http://localhost:7000/generate_response_informed"
rasp_pi_api_url = "https://humane-marmot-entirely.ngrok-free.app/"
headers = {"Content-Type": "application/json"}

# Directory to store session CSV files
session_csv_dir = "session_csv_files"
os.makedirs(session_csv_dir, exist_ok=True)

# In-memory history of the conversation (last 4 prompts and responses)
conversation_history = []
full_conversation_history = []
csv_file_path = None
time_responses_sent = None
time_chosen_response_received = None

# Eastern Time zone with DST handling (Eastern Time with DST awareness)
ET = pytz.timezone('US/Eastern')

# Generate a unique filename for each session
def generate_csv_filename():
    timestamp = datetime.now(ET).strftime("%Y%m%d_%H%M%S")
    return os.path.join(session_csv_dir, f"conversation_history_{timestamp}.csv")

def get_speech_to_text():
    data = requests.get(f'{rasp_pi_api_url}/get_audio_transcription')
    data_json = data.json()
    return data_json

def check_last_entry(history):
    if history and history[-1][1] is None:
        logging.warning("Incomplete entry found in conversation history.")
        return handle_incomplete_entry(history)
    return None

def handle_incomplete_entry(history):
    incomplete_entry = history.pop()
    return f"Didn't choose a response; removed: {incomplete_entry[0]}"

def initialize_csv_file(path):
    try:
        with open(path, 'w', newline='', encoding='utf-8') as file:
            writer = csv.writer(file)
            writer.writerow(['index', 'date_time', 'prompt', 'history', 'responses', 'chosen_response', 'emotion', 'personalize', 'server_to_pi_latency', 'pi_processing_latency', 'pi_to_server_latency', 'api_latency', 'chosen_response_latency'])
    except Exception as e:
        logging.error(f"Failed to create CSV: {e}")

def append_to_csv_file(path, entry):
    try:
        with open(path, 'a', newline='', encoding='utf-8') as file:
            writer = csv.writer(file)
            (index, date_time, partner_prompt, model_responses, user_response, history_snapshot, emotion, personalize, server_to_pi_latency, pi_processing_latency, pi_to_server_latency, api_latency, chosen_response_latency) = entry
            responses_list = model_responses.get('responses', [])
            history_str = ";\n".join(["\n".join(map(str, pair)) for pair in (history_snapshot or [])])
            responses_str = ';\n'.join(map(str, responses_list))
            writer.writerow([str(index), date_time, str(partner_prompt), history_str, responses_str, str(user_response), str(emotion), str(personalize), str(server_to_pi_latency), str(pi_processing_latency), str(pi_to_server_latency), str(api_latency), str(chosen_response_latency)])
    except Exception as e:
        logging.error(f"Failed to append to CSV: {e}")

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    global csv_file_path, conversation_history, full_conversation_history
    csv_file_path = generate_csv_filename()
    conversation_history = []
    full_conversation_history = []
    initialize_csv_file(csv_file_path)

    await websocket.accept()
    try:
        while True:
            data = await websocket.receive_text()
            if data:
                time_received_osdpi = datetime.now(ET)
                print(f'Data received from OS-DPI at {time_received_osdpi}')
            try:
                data_json = json.loads(data)
                state = data_json.get("state", {})
                prefix = state.get("$prefix", "")
                emotion = state.get("$Style", "")
                personalize = state.get("$personalize", None)

                if prefix == 'prompt':
                    incomplete_message = check_last_entry(conversation_history)
                    
                    time_server_sent_to_rasp_pi = datetime.now(ET)
                    rasp_pi_data = get_speech_to_text()
                    time_server_received_from_rasp_pi = datetime.now(ET)
                    prompt = rasp_pi_data['text']
                    time_rasp_pi_received_from_server = parse(rasp_pi_data['time_received']).astimezone(ET)
                    time_rasp_pi_sent_to_server = parse(rasp_pi_data['time_processed']).astimezone(ET)
                    time_rasp_pi_processing = rasp_pi_data['total_time']

                    server_to_pi_latency = (time_rasp_pi_received_from_server - time_server_sent_to_rasp_pi).total_seconds()
                    pi_processing_latency = time_rasp_pi_processing
                    pi_to_server_latency = (time_server_received_from_rasp_pi - time_rasp_pi_sent_to_server).total_seconds()

                    print('prompt is ', prompt)
                    message = json.dumps({'state': {"$Display": prompt}})
                    print(f'time_server_sent_to_rasp_pi - {time_server_sent_to_rasp_pi}')
                    print(f'time_server_received_from_rasp_pi - {time_server_received_from_rasp_pi}')
                    print(f'time_rasp_pi_received_from_server - {time_rasp_pi_received_from_server}')
                    print(f'time_rasp_pi_sent_to_server - {time_rasp_pi_sent_to_server}')

                    print(f'time taken for request to reach rasp pi from server - {server_to_pi_latency}')
                    print(f'time taken rasp pi and nova to process request - {pi_processing_latency}')
                    print(f'time taken for request to reach server from rasp pi - {pi_to_server_latency}')

                    print(f'total time taken for request to leave and reach server - {str(time_server_received_from_rasp_pi - time_server_sent_to_rasp_pi)}')

                    await websocket.send_text(message)

                    if prompt:
                        print('inside prompt if')
                        logging.info(f"Received prompt: {prompt} | Emotion: {emotion} | Personalize: {personalize}")
                        loop = asyncio.get_running_loop()

                        # Construct the full prompt using the last 4 exchanges (3 previous + current)
                        full_prompt = "\n".join(["\n".join(map(str, pair)) for pair in conversation_history[-4:] if pair[1] is not None]) + "\n" + prompt
                        print(full_prompt)
                        logging.info(f"Sending full prompt to model: {full_prompt}")
                        
                        api_request_start_time = datetime.now(ET)
                        response = await loop.run_in_executor(executor, send_to_api_sync, full_prompt, emotion, personalize)
                        api_request_end_time = datetime.now(ET)
                        
                        api_latency = (api_request_end_time - api_request_start_time).total_seconds()

                        responses_list = response.get('responses', [])

                        responses_dict = {f"response{i+1}": resp for i, resp in enumerate(responses_list)}
                        responses_dict['Display'] = prompt
                        if incomplete_message:
                            responses_dict['warning'] = incomplete_message
                        print(responses_dict)
                        time_responses_sent = datetime.now(ET)
                        await websocket.send_text(json.dumps(responses_dict))

                        update_history(conversation_history, prompt, None, response, full_conversation_history, emotion, personalize)
                        
                    else:
                        logging.error("No prompt found in the received data.")
                elif prefix == 'Chosen':
                    chosen_response = state.get("$socket", "")
                    time_chosen_response_received = datetime.now(ET)
                    chosen_response_latency = (time_chosen_response_received - time_responses_sent).total_seconds()


                    if chosen_response:
                        logging.info(f"Received chosen response: {chosen_response}")
                        if conversation_history and conversation_history[-1][1] is None:
                            conversation_history[-1] = (conversation_history[-1][0], chosen_response)
                            update_full_history(full_conversation_history, conversation_history[-1], chosen_response)
                            timestamp = datetime.now(ET).strftime("%Y-%m-%d %H:%M:%S")
                            append_to_csv_file(csv_file_path, (len(conversation_history), timestamp, conversation_history[-1][0], full_conversation_history[-1][1], chosen_response, conversation_history[-4:], full_conversation_history[-1][4], full_conversation_history[-1][5], server_to_pi_latency, pi_processing_latency, pi_to_server_latency, api_latency, chosen_response_latency))
                        else:
                            logging.error("Chosen response received without a corresponding prompt.")
                    else:
                        logging.error("No chosen response found in the received data.")

                elif prefix == 'regenerate':
                    prompt = state.get("$prompt", "")
                    if not prompt:
                        logging.error("No prompt provided for regeneration.")
                        continue

                    logging.info(f"Regenerating responses for prompt: {prompt} | Emotion: {emotion}")

                    # Construct the full prompt using the last 4 exchanges (excluding the unwanted response)
                    full_prompt = "\n".join(["\n".join(map(str, pair)) for pair in conversation_history[-4:-1] if pair[1] is not None]) + "\n" + prompt

                    response = await loop.run_in_executor(executor, send_to_api_sync_regen, full_prompt, emotion, personalize)
                    responses_list = response.get('responses', [])
                    responses_dict = {f"response{i+1}": resp for i, resp in enumerate(responses_list)}
                    responses_dict['Display'] = prompt

                    time_responses_sent = datetime.now(ET)
                    await websocket.send_text(json.dumps(responses_dict))

                    # Update history with the new prompt only (without appending the unwanted response)
                    conversation_history[-1] = (prompt, None)
                    update_history(conversation_history, prompt, None, response, full_conversation_history, emotion, personalize)

                elif prefix == 'new_conv':
                    logging.info("Received new_conv prefix, clearing conversation history and starting new conversation.")
                    conversation_history.clear()

                else:
                    logging.error(f"Unexpected prefix value: {prefix}")

            except json.JSONDecodeError:
                logging.error("Invalid JSON received.")
            except Exception as e:
                logging.error(f"An error occurred: {e}")
    except WebSocketDisconnect:
        logging.info("WebSocket disconnected")

def update_history(history, partner_prompt, user_response, model_responses, full_history, emotion, personalize):
    history_snapshot = history[-4:]
    while len(history) > 4:
        history.pop(0)
    history.append((partner_prompt, user_response))
    if model_responses is not None:
        full_history.append((partner_prompt, model_responses, user_response, history_snapshot, emotion, personalize))

def update_full_history(full_history, last_convo_pair, chosen_response):
    for index, (partner_prompt, model_responses, user_response, history_snapshot, emotion, personalize) in enumerate(full_history):
        if partner_prompt == last_convo_pair[0] and user_response is None:
            full_history[index] = (partner_prompt, model_responses, chosen_response, history_snapshot, emotion, personalize)
            break

@app.get("/download_csv")
async def download_csv():
    global csv_file_path
    try:
        return FileResponse(csv_file_path, media_type='text/csv', filename=os.path.basename(csv_file_path))
    except Exception as e:
        logging.error(f"Failed to generate CSV: {e}")
        return {"error": f"Failed to generate CSV: {e}"}

def send_to_api_sync(prompt, emotion, personalize):
    try:
        payload = {'prompt': prompt, 'emotion': emotion, 'personalize': personalize}
        response = requests.post(api_url, headers=headers, json=payload)
        logging.info(f"Sending payload to API: {payload}")
        response.raise_for_status()
        print(response)
        return response.json()
    except requests.HTTPError as e:
        logging.error(f"HTTP error occurred: {e.response.status_code} - {e.response.text}")
        return []
    except requests.RequestException as e:
        logging.error(f"Error sending request to API: {e}")
        return []


def send_to_api_sync_regen(full_prompt, emotion, personalize):
    responses = []
    try:
        # Define two lengths for responses: 15 words and 7 words
        lengths = [15, 7]
        for length in lengths:
            for _ in range(2):  # Generate two responses for each length
                # Construct the template with the full prompt
                template_text = f"\
                Use the following pieces of context to continue the chit-chat conversation. You are answering as the User.\
                Keep the response as concise as possible.\
                Dialogue: {{full_prompt}}\
                Emotion Tone: {{emotion}}\
                Keep it strictly within this number of words: {{length}}\
                Response:"
                
                template = PromptTemplate(
                    input_variables=["full_prompt", "emotion", "length"],
                    template=template_text
                )
                
                prompt_with_template = template.format(
                    full_prompt=full_prompt,
                    emotion=emotion if emotion else "Neutral",
                    length=length
                )
                
                # Generate response from GPT-4
                response = llm(prompt_with_template)
                responses.append(response.content.strip())

        return {'responses': responses}
    except Exception as e:
        logging.error(f"Error generating responses: {str(e)}")
        return {'responses': []}


if __name__ =="__main__":
    uvicorn.run(app, host="0.0.0.0", port=5678, log_level="info")

