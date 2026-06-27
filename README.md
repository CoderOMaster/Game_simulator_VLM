# Mafia III Autonomous Driving via LLM

An experimental project to see if a state of the art Large Language Model (LLM) can autonomously drive a vehicle in the game Mafia III using real time screen capture and a virtual gamepad.

## The Project Idea
The goal of this project was to push the limits of Vision Language Models by giving them full control of a vehicle in an open world game. By feeding the LLM real time screenshots of the road ahead and prompting it with driving rules, the idea was to see if it could process the visual data fast enough and accurately enough to steer, accelerate, brake, and avoid collisions autonomously.

## Results & Post Mortem
**TL;DR: The latency was surprisingly good, but the visual reasoning was not there.**

* **The Model:** This experiment utilized `gpt-5.4-mini`.
* **The Good (Latency):** Surprisingly, the round trip time (capture to encode to API call to JSON parse to controller input) was highly manageable. The script maintained a solid call rate that was fast enough for real time control.
* **The Bad (Reasoning):** The driving results were not good. As expected with current vision models, the LLM struggled significantly with spatial awareness and temporal continuity. Because the AI evaluates every frame in isolation, it lacks the context of momentum, depth perception, and approach speed. It often failed to recognize immediate obstacles or overcorrected steering because it could not reliably map a 2D image to 3D driving mechanics.

## How It Works
1. **Screen Capture:** Uses `mss` to grab high speed frames of the Mafia III game window.
2. **Vision Processing:** `OpenCV` crops the frame to the relevant road view (removing skies and UI), downscales it, and compresses it to a lightweight JPEG to save bandwidth.
3. **Inference Engine:** The image is sent to the OpenAI API as a base64 string alongside a strict system prompt containing driving rules and a required JSON schema.
4. **Virtual Controller:** The parsed JSON commands (e.g., `["w", "a"]` for gas + left) are mapped to an emulated Xbox 360 controller using `vgamepad`, injecting the inputs directly into the game.
5. **Telemetry HUD:** A transparent `tkinter` overlay sits on top of the game, displaying the AI current inputs, reasoning, API latency, and call rate in real time.

## Setup & Installation

### Prerequisites
* Windows 10/11
* Python 3.8+
* Mafia III (Running in windowed or borderless mode)

### 1. Install Dependencies
```bash
pip install mss opencv-python keyboard vgamepad numpy openai

```

### 2. Emulated Gamepad Drivers

Because this uses `vgamepad`, it relies on the ViGEmBus driver to emulate an Xbox 360 controller. If you do not have it installed, the `vgamepad` package will usually prompt you or you can install it manually from the [Nefarius ViGEmBus repository](https://github.com/nefarius/ViGEmBus).

### 3. API Key

Create a `.env` file in the root directory and add your OpenAI API key:

```env
OPENAI_API_KEY=your_api_key_here

```

## Usage

1. Launch Mafia III.
2. Run the Python script:
```bash
python autonomous_mafia.py

```


3. You have 3 seconds to focus the game window. The transparent HUD will appear, and the AI will take over the controller.
4. Press Q at any time to trigger the kill switch and stop the script.

## Future Improvements

While the single frame LLM approach struggled, future iterations of this concept could explore:

* **Frame Stacking:** Sending the last 3 to 4 frames as a GIF or video input to give the model temporal awareness and a sense of velocity.
* **Fine Tuned SLMs:** Training a much smaller, specialized model specifically on driving gameplay data rather than using a generalized conversational LLM.
* **Depth Mapping:** Running a localized depth estimation model before sending the image to the LLM to give it explicit numerical distance data to obstacles.

## Video Demo 

https://vimeo.com/1205142451?share=copy&fl=sv&fe=ci

