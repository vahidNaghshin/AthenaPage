# AthenaPage

This repository contains:
- A Chainlit app that answers questions about the current webpage.
- A Chrome extension that sends page context (URL, title, and visible text) to the Chainlit backend.

The architecture is model-provider agnostic and can be used with any model API.
This version is focused on AWS Bedrock models, but the LLM wiring can be modified quickly to use other providers.

## Repo Structure

- `app.py`: Chainlit backend and Bedrock chat logic.
- `my_extentions/`: Chrome extension files (`manifest.json`, `content.js`, `content.css`, `icons/`).
- `requirement.txt`: Python dependencies (currently generated with `pip freeze`).
- `chainlit.md`: Chainlit welcome content.

## Prerequisites

- Python 3.10+ (3.11 recommended)
- AWS account with Bedrock model access
- AWS CLI configured with a working profile
- Google Chrome (for loading the extension)

## 1. Install Python Dependencies

From the repository root:

```bash
python -m pip install -r requirement.txt
```

## 2. Configure AWS Bedrock Authentication

The app reads these environment variables:
- `AWS_PROFILE`
- `AWS_REGION` (defaults to `us-west-2` if not set)
- `BEDROCK_MODEL_ID` (required)

Before running, set your environment in the shell:

```bash
export AWS_PROFILE=your-aws-profile
export AWS_REGION=us-west-2
export BEDROCK_MODEL_ID=your-bedrock-model-id
```

For public repos, avoid committing organization-specific profile names or account details.

Optional quick auth check:

```bash
aws sts get-caller-identity --profile "$AWS_PROFILE"
```

Notes:
- The selected AWS profile must have permission to call Bedrock and STS.
- The model ID must be enabled for your account in the configured region.

## 3. Run the Chainlit App

Start Chainlit on port 8000 (used by the extension):

```bash
chainlit run app.py --port 8000 -w
```

The app should be available at:
- `http://localhost:8000`

## 4. Load the Chrome Extension

1. Open Chrome and go to `chrome://extensions`.
2. Enable Developer mode.
3. Click Load unpacked.
4. Select the `my_extentions` folder.
5. Open any webpage and click the floating Ask This Page button.

## How It Works

1. The extension extracts visible page text.
2. It POSTs context to `POST /ext/context` on the Chainlit server.
3. Chainlit initializes a chat model (Bedrock in this implementation) and stores the page context as a system prompt.
4. User questions are answered using the captured page content.

## Troubleshooting

- `BEDROCK_MODEL_ID is not set`:
  - Export `BEDROCK_MODEL_ID` in your shell before starting Chainlit.
- `AWS credentials were not found`:
  - Confirm your profile exists and is valid (`aws configure list-profiles`).
  - Check identity with `aws sts get-caller-identity --profile "$AWS_PROFILE"`.
- Extension cannot connect:
  - Ensure Chainlit is running on `http://localhost:8000`.
  - Confirm extension host permissions in `my_extentions/manifest.json`.

