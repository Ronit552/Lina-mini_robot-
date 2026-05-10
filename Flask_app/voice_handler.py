import json
import threading
import queue
import time
import os
import vosk
import pyaudio

# Configure Vosk model path
MODEL_PATH = "model"

# Queue for audio data
audio_queue = queue.Queue()
_is_listening = False
_thread = None
_stream = None
_pa = None
_on_transcription_callback = None

def _audio_callback(in_data, frame_count, time_info, status):
    if _is_listening:
        audio_queue.put(in_data)
    return (None, pyaudio.paContinue)

def _voice_worker():
    global _is_listening
    
    # Wait until the model directory exists
    while not os.path.exists(MODEL_PATH) or not os.path.exists(os.path.join(MODEL_PATH, "vosk-model-small-en-us-0.15")):
        # We need the actual model folder inside "model". Wait, the extraction created 'model/vosk-model-small-en-us-0.15'
        # Let's check for any folder inside 'model' that looks like a vosk model
        if os.path.exists(MODEL_PATH):
            subdirs = [os.path.join(MODEL_PATH, d) for d in os.listdir(MODEL_PATH) if os.path.isdir(os.path.join(MODEL_PATH, d))]
            if subdirs:
                # Use the first subdir as the model path
                model_dir = subdirs[0]
                break
        time.sleep(1)
    
    model_dir = subdirs[0] if subdirs else MODEL_PATH
    
    print(f"[VOICE] Loading Vosk model from {model_dir}...")
    try:
        model = vosk.Model(model_dir)
        rec = vosk.KaldiRecognizer(model, 16000)
    except Exception as e:
        print(f"[VOICE] Error loading model: {e}")
        return

    print("[VOICE] Vosk model loaded successfully.")

    while True:
        if not _is_listening:
            time.sleep(0.1)
            # clear the queue so we don't process old audio when unpaused
            while not audio_queue.empty():
                try:
                    audio_queue.get_nowait()
                except queue.Empty:
                    break
            continue

        try:
            data = audio_queue.get(timeout=0.5)
            if rec.AcceptWaveform(data):
                res = json.loads(rec.Result())
                text = res.get("text", "").strip()
                if text and _on_transcription_callback:
                    _on_transcription_callback(text)
            else:
                # Partial results can be ignored or sent if needed
                pass
        except queue.Empty:
            continue
        except Exception as e:
            print(f"[VOICE] Error processing audio: {e}")
            time.sleep(1)

def start_listening(callback=None):
    global _is_listening, _thread, _pa, _stream, _on_transcription_callback
    
    if callback:
        _on_transcription_callback = callback
        
    if _thread is None:
        _pa = pyaudio.PyAudio()
        try:
            _stream = _pa.open(format=pyaudio.paInt16,
                               channels=1,
                               rate=16000,
                               input=True,
                               frames_per_buffer=4000,
                               stream_callback=_audio_callback)
            _stream.start_stream()
        except Exception as e:
            print(f"[VOICE] Error opening microphone stream: {e}")
            return

        _thread = threading.Thread(target=_voice_worker, daemon=True)
        _thread.start()
        print("[VOICE] Audio stream started.")

    _is_listening = True
    print("[VOICE] Listening RESUMED.")

def stop_listening():
    global _is_listening
    _is_listening = False
    print("[VOICE] Listening PAUSED.")

def set_callback(callback):
    global _on_transcription_callback
    _on_transcription_callback = callback
