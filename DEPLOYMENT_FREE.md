# Free Multi-User Deployment Guide (Flask + Firebase)

This project is now prepared for free deployment with auto-publish on code updates.

## 1) Where to Deploy

Deploy on **Render (Free Web Service)**.

Reason:
- Free tier available
- Auto deploy from GitHub on every push
- Easy Python setup
- Supports environment variables for Firebase secrets

## 2) One-Time Setup

1. Push this project to a GitHub repository.
2. Go to Render dashboard -> New -> Blueprint.
3. Connect your GitHub repo.
4. Render will detect `render.yaml` automatically.
5. Create service.

## 3) Required Environment Variables

In Render service settings, set:

- `FIREBASE_SERVICE_ACCOUNT_JSON`: Full Firebase service account JSON as a single-line JSON string
- `CAMERA_MODE`: `browser`
- `FLASK_ENV`: `production`
- `SECRET_KEY`: any strong random string

Notes:
- Do not upload `serviceAccountKey.json` to GitHub.
- This app supports Firebase credentials directly from `FIREBASE_SERVICE_ACCOUNT_JSON`.

## 4) Camera for Multi-User (Cloud Mode)

When `CAMERA_MODE=browser`:
- Each user uses their own browser webcam.
- Browser captures frames and sends them to `/api/recognize_frame`.
- Backend recognizes faces and marks attendance in Firebase.
- Existing server webcam stream stays intact for local mode (`CAMERA_MODE=server`).

## 5) Publishing Future Updates

1. Make code changes locally.
2. Commit and push to your GitHub main branch.
3. Render auto-redeploys.
4. Same production URL serves the updated app.

## 6) Local Development

If you want old local webcam behavior:
- Set `CAMERA_MODE=server`
- Run app locally as usual

If you want to test cloud-like flow locally:
- Set `CAMERA_MODE=browser`
- Run and open app in browser, allow camera permission

## 7) Important Functional Constraint

If you use ephemeral free hosting, local file folders such as `dataset/` are not persistent across restarts.
For long-term model persistence, keep your trained model in a persistent store (for example Firebase Storage) and load it at startup.
