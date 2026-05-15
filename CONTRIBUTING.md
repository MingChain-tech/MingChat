# Contributing to MingChat

Thank you for your interest in contributing to MingChat!

## How to Contribute

### Reporting Bugs

If you find a bug, please create an issue with:
- Clear description of the problem
- Steps to reproduce
- Expected vs actual behavior
- Your environment (OS, Python version, etc.)

### Suggesting Features

We welcome feature suggestions! Please create an issue with:
- Clear description of the feature
- Use case / motivation
- Any relevant examples or mockups

### Pull Requests

1. **Fork the repository**
2. **Create a feature branch**: `git checkout -b feature/your-feature-name`
3. **Make your changes** with appropriate tests
4. **Ensure tests pass**: `python tests/test_protocol.py`
5. **Commit your changes**: `git commit -m "feat: add your feature"`
6. **Push to your fork**: `git push origin feature/your-feature-name`
7. **Open a Pull Request**

### Code Style

- Follow PEP 8 guidelines
- Use meaningful variable and function names
- Add docstrings to public functions
- Keep functions focused and small

### Development Setup

```bash
# Clone your fork
git clone https://github.com/mingchain/mingchat.git
cd mingchat

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -e ".[dev]"

# Run tests
python tests/test_protocol.py
```

## License

By contributing to MingChat, you agree that your contributions will be licensed under the MIT License.
