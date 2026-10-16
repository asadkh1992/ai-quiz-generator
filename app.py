
import io
import json
from xml.sax.saxutils import escape

import pandas as pd
import streamlit as st
from groq import Groq
from supabase import create_client
from pypdf import PdfReader
from docx import Document
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet


st.set_page_config(
    page_title="QuizCraft AI",
    page_icon="📝",
    layout="wide",
)

FREE_GENERATION_LIMIT = 3


# ---------------- SUPABASE CLIENTS ----------------

@st.cache_resource
def get_admin_client(url, secret_key):
    # Server-side database access only. Never expose this key.
    return create_client(url, secret_key)


def get_auth_client():
    # Use the publishable key for email OTP authentication.
    return create_client(
        st.secrets["SUPABASE_URL"],
        st.secrets["SUPABASE_PUBLISHABLE_KEY"],
    )


def get_usage(admin, user_id):
    result = (
        admin.table("user_usage")
        .select("successful_generations")
        .eq("user_id", user_id)
        .limit(1)
        .execute()
    )
    if result.data:
        return int(result.data[0]["successful_generations"])
    return 0



# --- QuizCraft AI: Email + Password Authentication ---
import streamlit as st

auth = get_auth_client()

if "user_id" not in st.session_state:
    st.session_state.user_id = None
    st.session_state.user_email = None

if not st.session_state.user_id:
    st.title("QuizCraft AI")
    st.subheader("Sign in or create your account")

    signin_tab, signup_tab = st.tabs(["Sign in", "Create account"])

    with signin_tab:
        with st.form("signin_form"):
            login_email = st.text_input("Email", key="login_email")
            login_password = st.text_input(
                "Password", type="password", key="login_password"
            )
            signin_clicked = st.form_submit_button(
                "Sign in", use_container_width=True
            )

        if signin_clicked:
            try:
                result = auth.auth.sign_in_with_password({
                    "email": login_email.strip(),
                    "password": login_password,
                })

                user = result.user
                if user and getattr(user, "email_confirmed_at", None):
                    st.session_state.user_id = str(user.id)
                    st.session_state.user_email = user.email
                    st.rerun()
                else:
                    st.error(
                        "Please verify your email before accessing QuizCraft AI."
                    )

            except Exception as e:
                st.error(f"Sign-in failed: {e}")
    
    with signup_tab:
        with st.form("signup_form"):
            signup_email = st.text_input("Email", key="signup_email")
            signup_password = st.text_input(
                "Create password (at least 8 characters)",
                type="password",
                key="signup_password",
            )
            signup_confirm = st.text_input(
                "Confirm password",
                type="password",
                key="signup_confirm",
            )
            signup_clicked = st.form_submit_button(
                "Create account", use_container_width=True
            )

        if signup_clicked:
            email = signup_email.strip()

            if not email or "@" not in email:
                st.error("Please enter a valid email address.")
            elif len(signup_password) < 8:
                st.error("Your password must be at least 8 characters.")
            elif signup_password != signup_confirm:
                st.error("The passwords do not match.")
            else:
                try:
                    result = auth.auth.sign_up({
                        "email": email,
                        "password": signup_password,
                    })

                    if result.user and not result.session:
                        st.success(
                            "Account created! Check your email and click "
                            "the verification link. Then return here and "
                            "sign in with your email and password."
                        )
                    elif result.user and result.session:
                        # Do not grant access unless email is verified.
                        if getattr(
                            result.user, "email_confirmed_at", None
                        ):
                            st.session_state.user_id = str(result.user.id)
                            st.session_state.user_email = result.user.email
                            st.rerun()
                        else:
                            st.warning(
                                "Please verify your email before signing in."
                            )
                    else:
                        st.error(
                            "Account creation did not complete. Please try again."
                        )

                except Exception:
                    st.error(
                        "Could not create the account. The email may already "
                        "be registered, or email delivery may be unavailable. "
                        "Please try signing in or try again later."
                    )

    st.info(
        "New users must verify their email before signing in. "
        "After verification, return to this page and sign in."
    )
    st.stop()

# The user is signed in. Continue to your existing quiz-generation app below.
st.sidebar.write(f"Signed in: {st.session_state.user_email}")

if st.sidebar.button("Sign out"):
    st.session_state.user_id = None
    st.session_state.user_email = None
    st.rerun()


# ---------------- SIGNED-IN USER ----------------

user_id = st.session_state.user_id
user_email = st.session_state.user_email or "Verified user"

with st.sidebar:
    st.success(f"Signed in: {user_email}")

    if st.button("Sign out", use_container_width=True):
        st.session_state.user_id = None
        st.session_state.user_email = None
        st.session_state.otp_email = ""
        st.session_state.otp_sent = False
        st.session_state.quiz_df = None
        st.rerun()

try:
    used_count = get_usage(admin, user_id)
except Exception as exc:
    st.error(
        "Could not read your usage record. "
        "Check the Supabase table and server-side key. "
        f"Details: {type(exc).__name__}: {exc}"
    )
    st.stop()

remaining = max(0, FREE_GENERATION_LIMIT - used_count)
st.info(
    f"Free generations used: {used_count}/{FREE_GENERATION_LIMIT}. "
    f"Remaining: {remaining}."
)

if remaining == 0:
    st.warning(
        "You've used your three free quiz generations. "
        "Subscription billing is not connected yet."
    )

st.info(
    "Prototype: review every generated question and answer "
    "before using a quiz for teaching or assessment."
)


# ---------------- SOURCE EXTRACTION ----------------

def extract_text(uploaded_file):
    name = uploaded_file.name.lower()

    if name.endswith(".pdf"):
        reader = PdfReader(uploaded_file)
        return "\n".join(
            page.extract_text() or "" for page in reader.pages
        )

    if name.endswith(".docx"):
        document = Document(uploaded_file)
        return "\n".join(
            paragraph.text for paragraph in document.paragraphs
        )

    if name.endswith(".txt"):
        return uploaded_file.getvalue().decode(
            "utf-8", errors="replace"
        )

    raise ValueError("Unsupported file format.")


# ---------------- QUIZ GENERATION ----------------

def generate_quiz(
    source_text,
    subject,
    level,
    difficulty,
    question_type,
    number_of_questions,
    language,
):
    client = Groq(api_key=st.secrets["GROQ_API_KEY"])

    prompt = f"""
Create a high-quality quiz using ONLY the learning material below.
Do not invent facts unsupported by the material.
If the material is insufficient, make fewer questions rather than
inventing unsupported answers.

Subject: {subject}
Learner level: {level}
Difficulty: {difficulty}
Question format: {question_type}
Requested number of questions: {number_of_questions}
Language: {language}

Return valid JSON only, using this structure:
{{
  "questions": [
    {{
      "question": "Question text",
      "options": ["Option A", "Option B", "Option C", "Option D"],
      "correct_answer": "Exact correct option or answer",
      "explanation": "Brief explanation grounded in the material"
    }}
  ]
}}

Rules:
- For multiple choice, provide exactly four options and one answer.
- For true/false, use options ["True", "False"].
- For short answer, use an empty options list.
- The correct_answer must exactly match an option for MCQ and true/false.
- Avoid duplicate questions.
- Keep explanations concise.
- Do not include markdown fences around JSON.

LEARNING MATERIAL:
{source_text}
"""

    response = client.chat.completions.create(
        model="openai/gpt-oss-20b",
        messages=[
            {
                "role": "system",
                "content": (
                    "You are an educational assessment assistant. "
                    "Use the supplied material and return valid JSON only."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        temperature=0.2,
        max_completion_tokens=6000,
        response_format={"type": "json_object"},
    )

    data = json.loads(response.choices[0].message.content)
    questions = data.get("questions", [])

    if not isinstance(questions, list) or not questions:
        raise ValueError("The AI returned no usable questions.")

    clean_questions = []

    for item in questions:
        if not isinstance(item, dict):
            continue

        question = str(item.get("question", "")).strip()
        if not question:
            continue

        options = item.get("options", [])
        if not isinstance(options, list):
            options = []

        clean_questions.append({
            "Question": question,
            "Option A": str(options[0]) if len(options) > 0 else "",
            "Option B": str(options[1]) if len(options) > 1 else "",
            "Option C": str(options[2]) if len(options) > 2 else "",
            "Option D": str(options[3]) if len(options) > 3 else "",
            "Correct answer": str(
                item.get("correct_answer", "")
            ).strip(),
            "Explanation": str(item.get("explanation", "")).strip(),
        })

    if not clean_questions:
        raise ValueError("No valid questions were returned.")

    return pd.DataFrame(clean_questions)


# ---------------- WORD AND PDF EXPORT ----------------

def make_docx(df, title):
    document = Document()
    document.add_heading(title, 0)

    for index, row in df.iterrows():
        document.add_heading(f"Question {index + 1}", level=2)
        document.add_paragraph(str(row["Question"]))

        for column in ["Option A", "Option B", "Option C", "Option D"]:
            value = str(row[column]).strip()
            if value and value.lower() != "nan":
                document.add_paragraph(f"{column}: {value}")

        document.add_paragraph(f"Answer: {row['Correct answer']}")

        explanation = str(row["Explanation"]).strip()
        if explanation and explanation.lower() != "nan":
            document.add_paragraph(f"Explanation: {explanation}")

    output = io.BytesIO()
    document.save(output)
    return output.getvalue()


def make_pdf(df, title):
    output = io.BytesIO()
    pdf = SimpleDocTemplate(
        output,
        pagesize=letter,
        rightMargin=45,
        leftMargin=45,
        topMargin=45,
        bottomMargin=45,
    )
    styles = getSampleStyleSheet()

    story = [
        Paragraph(escape(str(title)), styles["Title"]),
        Spacer(1, 16),
    ]

    for index, row in df.iterrows():
        story.append(
            Paragraph(f"<b>Question {index + 1}</b>", styles["Heading3"])
        )
        story.append(
            Paragraph(escape(str(row["Question"])), styles["BodyText"])
        )

        for column in ["Option A", "Option B", "Option C", "Option D"]:
            value = str(row[column]).strip()
            if value and value.lower() != "nan":
                story.append(
                    Paragraph(
                        escape(f"{column}: {value}"),
                        styles["BodyText"],
                    )
                )

        story.append(
            Paragraph(
                "<b>Answer:</b> "
                + escape(str(row["Correct answer"])),
                styles["BodyText"],
            )
        )

        explanation = str(row["Explanation"]).strip()
        if explanation and explanation.lower() != "nan":
            story.append(
                Paragraph(
                    "<b>Explanation:</b> " + escape(explanation),
                    styles["BodyText"],
                )
            )

        story.append(Spacer(1, 12))

    pdf.build(story)
    return output.getvalue()


# ---------------- QUIZ INPUTS ----------------

with st.sidebar:
    st.header("Quiz settings")
    subject = st.text_input("Subject", value="General Science")
    level = st.selectbox(
        "Learner level",
        [
            "Primary school", "Middle school", "Secondary school",
            "College", "University", "Professional training",
        ],
    )
    difficulty = st.selectbox(
        "Difficulty", ["Easy", "Medium", "Hard", "Mixed"]
    )
    question_type = st.selectbox(
        "Question format",
        ["Multiple choice", "True/False", "Short answer"],
    )
    number_of_questions = st.slider(
        "Number of questions", 3, 15, 5
    )
    language = st.selectbox("Quiz language", ["English", "Urdu"])


uploaded_file = st.file_uploader(
    "Upload study material",
    type=["pdf", "docx", "txt"],
    help="Use a text-based PDF. Scanned PDFs may need OCR.",
)
pasted_text = st.text_area(
    "Or paste your notes here",
    height=180,
    placeholder="Paste your lesson, notes or study material...",
)

source_text = ""
source_name = "Pasted study material"

if uploaded_file is not None:
    try:
        source_text = extract_text(uploaded_file)
        source_name = uploaded_file.name
    except Exception as exc:
        st.error(f"Could not read the uploaded file: {exc}")

if pasted_text.strip():
    source_text = pasted_text.strip()
    source_name = "Pasted study material"

if source_text:
    st.caption(
        f"Source: {source_name} · {len(source_text):,} characters extracted"
    )
    if len(source_text) > 30000:
        st.warning(
            "Only the first 30,000 characters will be used."
        )
        source_text = source_text[:30000]


# ---------------- RESERVE, GENERATE, COMMIT ----------------

if st.button(
    "✨ Generate quiz",
    type="primary",
    use_container_width=True,
    disabled=(remaining <= 0),
):
    if not source_text.strip():
        st.warning("Upload a file or paste study material first.")
    elif len(source_text.strip()) < 100:
        st.warning("Please provide at least 100 characters of material.")
    else:
        reservation_id = None

        try:
            reservation = admin.rpc(
                "reserve_quiz_generation",
                {"p_user_id": user_id},
            ).execute()

            reservation_id = reservation.data

            if not reservation_id:
                st.warning(
                    "Your three free generations have been used, "
                    "or another generation is already in progress. "
                    "Please check your usage and try again."
                )
            else:
                with st.spinner("Creating your quiz..."):
                    new_df = generate_quiz(
                        source_text=source_text,
                        subject=subject,
                        level=level,
                        difficulty=difficulty,
                        question_type=question_type,
                        number_of_questions=number_of_questions,
                        language=language,
                    )

                committed = admin.rpc(
                    "commit_quiz_generation",
                    {"p_reservation_id": str(reservation_id)},
                ).execute()

                if not committed.data:
                    raise RuntimeError(
                        "The generation could not be recorded. "
                        "Please try again."
                    )

                st.session_state.quiz_df = new_df
                st.success(f"Generated {len(new_df)} questions.")
                st.rerun()

        except Exception as exc:
            if reservation_id:
                try:
                    admin.rpc(
                        "release_quiz_generation",
                        {"p_reservation_id": str(reservation_id)},
                    ).execute()
                except Exception:
                    pass

            st.error(
                "Quiz generation failed. If the issue persists, "
                "check your Groq model access, API limits and Supabase "
                f"configuration. Details: {type(exc).__name__}: {exc}"
            )


# ---------------- EDIT AND DOWNLOAD ----------------

if st.session_state.quiz_df is not None:
    st.divider()
    st.header("Review and edit your quiz")
    st.caption(
        "Click any cell to edit it. Check answers before distributing."
    )

    edited_df = st.data_editor(
        st.session_state.quiz_df,
        num_rows="dynamic",
        hide_index=True,
        use_container_width=True,
        key="quiz_editor",
        column_config={
            "Question": st.column_config.TextColumn(
                "Question", width="large", required=True
            ),
            "Explanation": st.column_config.TextColumn(
                "Explanation", width="large"
            ),
        },
    )
    st.session_state.quiz_df = edited_df

    quiz_title = st.text_input("Quiz title", value=f"{subject} Quiz")

    col1, col2 = st.columns(2)

    with col1:
        st.download_button(
            "Download Word (.docx)",
            data=make_docx(edited_df, quiz_title),
            file_name="quiz.docx",
            mime=(
                "application/vnd.openxmlformats-officedocument."
                "wordprocessingml.document"
            ),
            use_container_width=True,
        )

    with col2:
        st.download_button(
            "Download PDF",
            data=make_pdf(edited_df, quiz_title),
            file_name="quiz.pdf",
            mime="application/pdf",
            use_container_width=True,
        )

    if st.button("Clear quiz"):
        st.session_state.quiz_df = None
        st.rerun()

st.divider()
st.caption(
    "QuizCraft AI prototype · Review generated content before use."
)
