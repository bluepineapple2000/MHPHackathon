from flask import Flask, render_template

app = Flask(__name__)

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/impressum')
def impressum():
    return render_template('impressum.html')

# ============================================
# FUNCTIONALITY WILL BE ADDED BELOW THIS LINE
# ============================================
# Add your routes and functions here as needed
# Examples for future features:
# - @app.route('/api/data', methods=['GET', 'POST'])
# - Database operations
# - Form handling
# - API endpoints
# ============================================

if __name__ == '__main__':
    app.run(debug=True, port=5000)
