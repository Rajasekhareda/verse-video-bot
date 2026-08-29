from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/spreadsheets",
]

flow = InstalledAppFlow.from_client_secrets_file("client_secret.json", SCOPES)
creds = flow.run_local_server(port=0)

print("\n--- COPY THESE INTO GITHUB SECRETS ---")
print("YT_CLIENT_ID:", "851490367666-t0snm105lhp8glkcrp2obn4p80fs8fb9.apps.googleusercontent.com")
print("YT_CLIENT_SECRET:", "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")
print("YT_REFRESH_TOKEN:", creds.refresh_token)
