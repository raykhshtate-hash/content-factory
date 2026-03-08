#!/bin/bash
set -e

PROJECT_ID="romina-content-factory-489121"
SERVICE_NAME="content-factory"
REGION="europe-west1"

echo "🚀 Starting deployment of $SERVICE_NAME to Google Cloud Run..."

# 1. Read .env file and format variables for gcloud
echo "📦 Reading environment variables from .env..."
if [ ! -f .env ]; then
    echo "❌ Error: .env file not found!"
    exit 1
fi

# Extract non-empty, non-comment lines, excluding GOOGLE_APPLICATION_CREDENTIALS
# (it will be set via secret mount instead)
ENV_VARS=$(grep -v '^#' .env | grep -v '^$' | grep -v '^GOOGLE_APPLICATION_CREDENTIALS=' | tr '\n' ',' | sed 's/,$//')

# 2. Deploy to Cloud Run
echo "☁️  Deploying container (this may take a few minutes)..."
gcloud run deploy "$SERVICE_NAME" \
  --source . \
  --project "$PROJECT_ID" \
  --region "$REGION" \
  --allow-unauthenticated \
  --no-cpu-throttling \
  --set-env-vars "$ENV_VARS" \
  --set-env-vars "GOOGLE_APPLICATION_CREDENTIALS=/secrets/service-account.json" \
  --set-secrets="/secrets/service-account.json=service-account-json:latest"

# 3. Get the deployed service URL
echo "🔍 Retrieving service URL..."
SERVICE_URL=$(gcloud run services describe "$SERVICE_NAME" \
  --platform managed \
  --region "$REGION" \
  --project "$PROJECT_ID" \
  --format 'value(status.url)')

echo "✅ Service successfully deployed at:"
echo "➡️  $SERVICE_URL"

# 4. Set Telegram Webhook
echo "🔗 Setting Telegram Webhook..."

# Try to find telegram token (TELEGRAM_BOT_TOKEN or BOT_TOKEN)
BOT_TOKEN=$(grep -E '^(TELEGRAM_BOT_TOKEN|BOT_TOKEN)=' .env | head -n 1 | cut -d '=' -f2)

if [ -n "$BOT_TOKEN" ]; then
    WEBHOOK_URL="${SERVICE_URL}/webhook"
    
    RESPONSE=$(curl -s -X POST "https://api.telegram.org/bot${BOT_TOKEN}/setWebhook" \
        -H "Content-Type: application/json" \
        -d '{"url": "'${WEBHOOK_URL}'", "allowed_updates": ["message", "callback_query"]}')
        
    if echo "$RESPONSE" | grep -q '"ok":true'; then
        echo "✅ Webhook successfully set to: $WEBHOOK_URL"
    else
        echo "⚠️ Failed to set webhook. Telegram API response:"
        echo "$RESPONSE"
    fi
else
    echo "⚠️ Warning: TELEGRAM_BOT_TOKEN not found in .env. Skipping webhook setup."
fi

echo "🎉 Deployment complete!"
