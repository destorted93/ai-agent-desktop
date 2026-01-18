# AI Agent

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

Desktop AI assistant with voice input, chat interface, encrypted history, and powerful tools.

## Storage and Security

- Chat history and memories are stored encrypted at:
  - `%APPDATA%/ai-agent-desktop/chat_history.enc`
  - `%APPDATA%/ai-agent-desktop/memories.enc`
- Encryption key (`data_key`) is stored in Windows Credential Manager:
  - Service: `ai-agent-desktop/data_key`, Username: `data_key`
- API Token saved from Settings is stored under:
  - Service: `ai-agent-desktop/api_token`, Username: `api_token`
- See `secure_storage/README.md` for details.

## Contributing

Contributions are welcome! Feel free to:
- Report bugs or issues
- Suggest new features
- Submit pull requests
- Improve documentation

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Author

**destorted93**
- GitHub: [@destorted93](https://github.com/destorted93)
- Repository: [ai-agent-desktop](https://github.com/destorted93/ai-agent-desktop)
