# ai-quiz-generator

# QuizCraft AI

An AI-powered quiz and study-material generator built with Python,
Streamlit and the Groq API.

## Features

- Upload PDF, DOCX or TXT learning material
- Paste notes directly into the app
- Generate multiple-choice, true/false or short-answer questions
- Choose learner level, difficulty, subject and language
- Edit generated questions, options, answers and explanations
- Export quizzes to Word and PDF

## Setup

1. Install Python 3.11 or a compatible supported version.
2. Install dependencies:

   pip install -r requirements.txt

3. Create `.streamlit/secrets.toml` locally with:

   GROQ_API_KEY = "your-groq-api-key"

4. Start the app:

   streamlit run app.py

Never commit API keys, passwords or secrets to GitHub.

## Important

This is an early prototype. Generated educational material must be
reviewed before use. Authentication, persistent free-use limits,
subscriptions and production security will be added in later versions.
