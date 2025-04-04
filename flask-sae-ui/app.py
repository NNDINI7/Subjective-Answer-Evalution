from flask import Flask, render_template, request, jsonify, session
import pandas as pd
import pickle
import tensorflow as tf
import gensim.downloader as api
from gensim.models import KeyedVectors
from nltk.tokenize import word_tokenize
from nltk.corpus import stopwords
from nltk.stem import WordNetLemmatizer
from tensorflow.keras.preprocessing.sequence import pad_sequences
from tensorflow.keras.preprocessing.text import Tokenizer
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
import nltk
import os

# Download necessary NLTK resources
nltk.download('punkt')
nltk.download('stopwords')
nltk.download('wordnet')

app = Flask(__name__)
app.secret_key = 'your_secret_key'

# Load Word2Vec model (Google News)
print("Loading Word2Vec model...")
model_path = api.load("word2vec-google-news-300", return_path=True)
word_vectors = KeyedVectors.load_word2vec_format(model_path, binary=True, limit=50000)

# Load dataset
try:
    df = pd.read_csv("../train/processed_dataset.csv")
    df.columns = df.columns.str.strip().str.lower()
    required_columns = {"question", "desired_answer"}
    if not required_columns.issubset(df.columns):
        raise KeyError(f"Required columns {required_columns} are missing!")
except Exception as e:
    print(f"Error loading dataset: {e}")
    df = pd.DataFrame(columns=["question", "desired_answer"])

# Load trained LSTM model
print("Loading LSTM model...")
custom_objects = {"mse": tf.keras.losses.MeanSquaredError()}  # Fix MSE loss loading
model = tf.keras.models.load_model("../train/sae.h5", custom_objects=custom_objects)

# Load the tokenizer
with open("../train/tokenizer.pkl", "rb") as handle:
    tokenizer = pickle.load(handle)

if not isinstance(tokenizer, Tokenizer):
    print("❌ Tokenizer is corrupted! Re-saving it...")
    tokenizer = Tokenizer()
    tokenizer.fit_on_texts(["This is a test sentence"])
    with open("../train/tokenizer.pkl", "wb") as handle:
        pickle.dump(tokenizer, handle)
    print("✅ New tokenizer.pkl saved. Try running app.py again!")

MAX_LENGTH = 50

# Initialize NLP tools
stop_words = set(stopwords.words("english"))
lemmatizer = WordNetLemmatizer()

def preprocess_text(text):
    """Tokenizes, removes stopwords, and lemmatizes the input text."""
    if not isinstance(text, str) or text.strip() == "":
        return ""

    tokens = word_tokenize(text.lower())
    tokens = [lemmatizer.lemmatize(word) for word in tokens if word.isalnum() and word not in stop_words]

    return " ".join(tokens)

# Compute Word Mover's Distance (WMD)
def compute_wmd(text1, text2):
    """Computes the WMD between two texts using Word2Vec embeddings."""
    text1_tokens = text1.split()
    text2_tokens = text2.split()

    if not text1_tokens or not text2_tokens:
        return float("inf")

    try:
        wmd = word_vectors.wmdistance(text1_tokens, text2_tokens)
        return max(0, min(wmd, 2))  # Normalize WMD score
    except Exception:
        return 2

# Compute Cosine Similarity
vectorizer = TfidfVectorizer()
if not df.empty:
    vectorizer.fit(df["desired_answer"].dropna().tolist())

def compute_cosine_similarity(text1, text2):
    """Computes the cosine similarity between two texts."""
    if not text1 or not text2:
        return 0.0

    try:
        text_vectors = vectorizer.transform([text1, text2])
        return max(0, min(cosine_similarity(text_vectors[0], text_vectors[1])[0][0], 1))
    except Exception:
        return 0.0

def get_random_question():
    """Get a random question and its desired answer from the dataset."""
    if df.empty:
        return "No questions available", "No answers available"

    random_row = df.sample(n=1).iloc[0]
    return random_row["question"], random_row["desired_answer"]

@app.route('/')
def index():
    session.clear()  # Reset session on home page
    return render_template('index.html')

@app.route('/about')
def about():
    return render_template('about.html')

@app.route('/demo', methods=['GET', 'POST'])
def demo():
    """Handles the demo page for evaluating answers."""

    if 'question' not in session or 'desired_answer' not in session:
        question, desired_answer = get_random_question()
        session['question'] = question
        session['desired_answer'] = desired_answer
    else:
        question = session['question']
        desired_answer = session['desired_answer']

    student_answer = ""
    result = None

    if request.method == 'POST':
        student_answer = request.form.get('student_answer', "")

        student_answer_processed = preprocess_text(student_answer)
        desired_answer_processed = preprocess_text(desired_answer)

        # Compute similarity metrics
        wmd_score = compute_wmd(student_answer_processed, desired_answer_processed)
        cosine_sim = compute_cosine_similarity(student_answer_processed, desired_answer_processed)

        # Convert text to sequence for LSTM model
        seq = tokenizer.texts_to_sequences([student_answer_processed])
        padded_seq = pad_sequences(seq, maxlen=MAX_LENGTH)

        # Predict score using trained LSTM model
        predicted_score = model.predict(padded_seq)[0][0]  # Expecting a score in range [0, 1]

        # Normalize similarity scores to 0-5
        wmd_normalized = (1 - min(wmd_score / 2, 1)) * 5
        cosine_normalized = cosine_sim * 5

        # Combine scores
        final_score = (0.6 * predicted_score * 5) + (0.4 * (wmd_normalized + cosine_normalized) / 2)

        final_score = max(0, min(5, final_score))  # Ensure within 0-5

        print("Student Answer Processed:", student_answer_processed)
        print("Desired Answer Processed:", desired_answer_processed)
        print("WMD Score:", wmd_score)
        print("Cosine Similarity:", cosine_sim)
        print("LSTM Predicted Score:", predicted_score)
        print("Final Score (out of 5):", final_score)

        result = {
            'score': round(final_score, 2),
            'cosine_similarity': round(cosine_sim, 2),
            'wmd_score': round(wmd_score, 2),
            'feedback': generate_feedback(final_score)
        }

    return render_template('demo.html', question=question, desired_answer=desired_answer, student_answer=student_answer,
                           result=result)

def generate_feedback(score):
    """Generates feedback based on the similarity score."""
    if score >= 4.5:
        return "Excellent! Your answer closely matches the expected response."
    elif score >= 3.5:
        return "Good job! Your answer covers most of the key points."
    elif score >= 2.5:
        return "Satisfactory. Your answer includes some key points but misses others."
    elif score >= 1.5:
        return "Needs improvement. Your answer is missing many key points."
    else:
        return "Your answer differs significantly from the expected response. Please review the material."

if __name__ == '__main__':
    app.run(debug=True)
