# Project Development Guidelines

## Table of Contents
1. [Coding Standards and Preferences](#coding-standards-and-preferences)
2. [Project Architecture Overview](#project-architecture-overview)
3. [Git Workflow Rules](#git-workflow-rules)
4. [Testing Requirements](#testing-requirements)
5. [Documentation Standards](#documentation-standards)

---

## Coding Standards and Preferences

### General Principles

**Write code for humans, not just machines.** Your code should be self-documenting through clear naming, consistent structure, and logical organization.

#### Naming Conventions

```typescript
// ✅ GOOD: Descriptive and intention-revealing
const MAX_RETRY_ATTEMPTS = 3;
const getUserById = (id: string) => { ... };
const isActiveUser = (user: User) => { ... };
class UserService { ... }
interface UserProfile { ... }

// ❌ BAD: Vague or abbreviated
const max = 3;
const getUser = (id: any) => { ... };
const check = (u: any) => { ... };
class Service { ... }
interface Data { ... }
```

#### Function Design

```typescript
// ✅ GOOD: Single responsibility, clear inputs/outputs
async function fetchUserOrders(
  userId: string,
  options: { limit?: number; status?: OrderStatus }
): Promise<Order[]> {
  // Implementation
}

// ❌ BAD: Too many parameters, unclear purpose
async function getData(uid, l, s, inc, exc, sort, page, size) {
  // Implementation
}
```

**Rules:**
- Functions should do **one thing** and do it well
- Maximum 3-4 parameters (use options object for more)
- Keep functions under 50 lines when possible
- Use early returns to reduce nesting

#### Error Handling

```typescript
// ✅ GOOD: Specific error types with context
class NotFoundError extends AppError {
  constructor(resource: string, id: string) {
    super(`Resource ${resource} with id ${id} not found`, 404);
  }
}

try {
  const user = await getUserById(userId);
} catch (error) {
  if (error instanceof NotFoundError) {
    logger.warn('User not found', { userId });
    return handleNotFound();
  }
  throw error; // Re-throw unexpected errors
}

// ❌ BAD: Silent failures or generic catches
try {
  const user = await getUserById(userId);
} catch (e) {
  console.log(e); // Lost context
  return null; // Silent failure
}
```

#### Code Organization

```typescript
// ✅ GOOD: Logical grouping within files
// 1. Imports (external, internal, types)
import express from 'express';
import { UserService } from '../services/user.service';
import type { RequestHandler } from '../types';

// 2. Constants
const DEFAULT_PAGE_SIZE = 20;

// 3. Types/Interfaces
interface UserResponse {
  id: string;
  email: string;
}

// 4. Main implementation
export const getUserHandler: RequestHandler = async (req, res) => {
  // Implementation
};

// 5. Helper functions (private to file)
function formatUserResponse(user: User): UserResponse {
  // Implementation
}
```

---

## Project Architecture Overview

### Directory Structure

```
project-root/
├── src/
│   ├── controllers/          # HTTP request handlers (thin layer)
│   ├── services/             # Business logic
│   ├── repositories/         # Data access layer
│   ├── models/               # Data models and schemas
│   ├── middleware/           # Express/connect middleware
│   ├── utils/                # Shared utilities
│   ├── config/               # Configuration management
│   ├── types/                # TypeScript type definitions
│   └── index.ts              # Application entry point
├── tests/
│   ├── unit/
│   ├── integration/
│   └── e2e/
├── docs/
├── scripts/
├── .env.example
├── docker-compose.yml
├── package.json
└── README.md
```

### Layer Responsibilities

```typescript
// Controller Layer - HTTP concerns only
// src/controllers/user.controller.ts
export class UserController {
  constructor(private userService: UserService) {}

  async getUser(req: Request, res: Response): Promise<void> {
    const userId = req.params.id;
    const user = await this.userService.findById(userId);
    res.json({ data: user });
  }
}

// Service Layer - Business logic
// src/services/user.service.ts
export class UserService {
  constructor(
    private userRepository: UserRepository,
    private emailService: EmailService
  ) {}

  async findById(id: string): Promise<User> {
    const user = await this.userRepository.findById(id);
    if (!user) {
      throw new NotFoundError('User', id);
    }
    return user;
  }

  async createUser(data: CreateUserDto): Promise<User> {
    // Business rules, validations, side effects
    await this.validateEmailUnique(data.email);
    const user = await this.userRepository.create(data);
    await this.emailService.sendWelcomeEmail(user);
    return user;
  }
}

// Repository Layer - Data access only
// src/repositories/user.repository.ts
export class UserRepository {
  constructor(private db: Database) {}

  async findById(id: string): Promise<User | null> {
    return this.db.query('SELECT * FROM users WHERE id = $1', [id]);
  }

  async create(data: CreateUserDto): Promise<User> {
    const result = await this.db.query(
      'INSERT INTO users (...) VALUES (...) RETURNING *',
      Object.values(data)
    );
    return result.rows[0];
  }
}
```

### Dependency Injection

```typescript
// ✅ GOOD: Explicit dependencies, easy to test
// src/container.ts
export const container = {
  userService: new UserService(
    new UserRepository(database),
    new EmailService(emailClient)
  ),
  userController: new UserController(container.userService)
};

// In your routes
router.get('/users/:id', 
  (req, res) => container.userController.getUser(req, res)
);
```

---

## Git Workflow Rules

### Branch Strategy

```
main
├── develop (optional for larger teams)
│   ├── feature/user-authentication
│   ├── feature/payment-integration
│   ├── bugfix/login-error-handling
│   └── hotfix/critical-security-patch
```

#### Branch Naming Convention

```bash
# ✅ GOOD
feature/add-user-registration
feature/implement-oauth-login
bugfix/fix-memory-leak-in-cache
hotfix/patch-security-vulnerability
refactor/simplify-auth-middleware
docs/update-api-documentation
test/add-integration-tests-for-api

# ❌ BAD
new-feature
fix
my-branch
test
```

### Commit Message Format

Follow the [Conventional Commits](https://www.conventionalcommits.org/) specification:

```
<type>(<scope>): <subject>

<body>

<footer>
```

#### Examples

```bash
# ✅ GOOD
feat(auth): add JWT token refresh endpoint

Implemented automatic token refresh mechanism to improve user experience.
Tokens now refresh 5 minutes before expiration.

Closes #123

# ✅ GOOD
fix(api): resolve null pointer in user serialization

The user serializer crashed when profile data was missing.
Added defensive checks and default values.

Fixes #456

# ❌ BAD
fixed stuff
updated code
wip
```

#### Commit Types

- `feat`: New feature
- `fix`: Bug fix
- `docs`: Documentation changes
- `style`: Code style changes (formatting, semicolons, etc.)
- `refactor`: Code refactoring without behavior change
- `test`: Adding or updating tests
- `chore`: Maintenance tasks, dependencies, build config

### Pull Request Guidelines

#### PR Template

```markdown
## Description
Brief description of changes

## Type of Change
- [ ] Bug fix
- [ ] New feature
- [ ] Breaking change
- [ ] Documentation update

## Testing
- [ ] Unit tests added/updated
- [ ] Integration tests added/updated
- [ ] Manual testing completed

## Checklist
- [ ] Code follows project standards
- [ ] Self-review completed
- [ ] Documentation updated
- [ ] No new warnings introduced

## Related Issues
Closes #123
```

#### Review Process

```bash
# Before requesting review
git rebase main                    # Ensure up-to-date
git run lint                       # Pass linting
npm run test                       # All tests pass
npm run build                      # Build succeeds

# Squash commits if needed
git rebase -i HEAD~3

# Force push after rebase (carefully!)
git push --force-with-lease
```

---

## Testing Requirements

### Testing Pyramid

```
        /\
       /  \      E2E Tests (10%)
      /----\     - Critical user journeys
     /      \    - Slow, expensive
    /--------\
   /          \   Integration Tests (20%)
  /------------\  - API endpoints
 /              \ - Database interactions
/----------------\
                  \  Unit Tests (70%)
                   \ - Pure functions
                    \ - Fast, isolated
```

### Unit Testing

```typescript
// ✅ GOOD: Isolated, fast, deterministic
// tests/unit/user.service.test.ts
describe('UserService', () => {
  let userService: UserService;
  let mockUserRepository: MockType<UserRepository>;
  let mockEmailService: MockType<EmailService>;

  beforeEach(() => {
    mockUserRepository = createMock<UserRepository>();
    mockEmailService = createMock<EmailService>();
    userService = new UserService(mockUserRepository, mockEmailService);
  });

  describe('createUser', () => {
    it('should create user and send welcome email', async () => {
      const userData = { email: 'test@example.com', name: 'Test' };
      const expectedUser = { id: '1', ...userData };

      mockUserRepository.create.mockResolvedValue(expectedUser);

      const result = await userService.createUser(userData);

      expect(result).toEqual(expectedUser);
      expect(mockUserRepository.create).toHaveBeenCalledWith(userData);
      expect(mockEmailService.sendWelcomeEmail).toHaveBeenCalledWith(expectedUser);
    });

    it('should throw error if email already exists', async () => {
      mockUserRepository.findByEmail.mockResolvedValue({ id: '1' });

      await expect(
        userService.createUser({ email: 'existing@example.com' })
      ).rejects.toThrow(ConflictError);
    });
  });
});
```

### Integration Testing

```typescript
// ✅ GOOD: Test real interactions
// tests/integration/user.api.test.ts
describe('User API', () => {
  let app: Express;
  let testDb: Database;

  beforeAll(async () => {
    testDb = await setupTestDatabase();
    app = createApp(testDb);
  });

  beforeEach(async () => {
    await testDb.clear();
  });

  afterAll(async () => {
    await testDb.close();
  });

  describe('POST /api/users', () => {
    it('should create user and return 201', async () => {
      const response = await request(app)
        .post('/api/users')
        .send({ email: 'test@example.com', name: 'Test' })
        .expect(201);

      expect(response.body.data).toMatchObject({
        email: 'test@example.com',
        name: 'Test'
      });
    });

    it('should return 400 for invalid email', async () => {
      await request(app)
        .post('/api/users')
        .send({ email: 'invalid', name: 'Test' })
        .expect(400);
    });
  });
});
```

### Test Coverage Requirements

```json
// package.json
{
  "scripts": {
    "test": "jest",
    "test:coverage": "jest --coverage",
    "test:ci": "jest --coverage --ci --maxWorkers=2"
  },
  "jest": {
    "coverageThreshold": {
      "global": {
        "branches": 80,
        "functions": 80,
        "lines": 80,
        "statements": 80
      }
    }
  }
}
```

### Test File Naming

```bash
# ✅ GOOD
src/services/user.service.ts
tests/unit/user.service.test.ts

src/utils/formatting.ts
tests/unit/formatting.test.ts

# Match source file structure in tests
src/features/auth/login.ts
tests/unit/features/auth/login.test.ts
```

---

## Documentation Standards

### README.md Structure

```markdown
# Project Name

Brief description of what the project does and why it exists.

## Table of Contents
- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)
- [Usage](#usage)
- [Configuration](#configuration)
- [API Reference](#api-reference)
- [Development](#development)
- [Testing](#testing)
- [Deployment](#deployment)
- [Contributing](#contributing)

## Features
- Feature 1
- Feature 2
- Feature 3

## Requirements
- Node.js >= 18.x
- PostgreSQL >= 14.x
- Redis >= 7.x

## Installation

```bash
# Clone repository
git clone https://github.com/org/project.git
cd project

# Install dependencies
npm install

# Setup environment
cp .env.example .env
# Edit .env with your configuration

# Run database migrations
npm run migrate

# Start development server
npm run dev
```

## Usage

```typescript
import { UserService } from './src/services';

const userService = new UserService(repository, emailService);
const user = await userService.findById('user-id');
```

## Configuration

| Variable | Description | Default | Required |
|----------|-------------|---------|----------|
| `PORT` | Server port | `3000` | No |
| `DATABASE_URL` | PostgreSQL connection string | - | Yes |
| `REDIS_URL` | Redis connection string | - | Yes |
| `JWT_SECRET` | JWT signing secret | - | Yes |

## API Reference

### Authentication

#### POST `/api/auth/login`

**Request:**
```json
{
  "email": "user@example.com",
  "password": "securepassword"
}
```

**Response (200):**
```json
{
  "data": {
    "token": "jwt-token-here",
    "user": {
      "id": "uuid",
      "email": "user@example.com"
    }
  }
}
```

## Development

```bash
# Start development server with hot reload
npm run dev

# Run linter
npm run lint

# Fix linting issues
npm run lint:fix

# Type checking
npm run type-check
```

## Testing

```bash
# Run all tests
npm test

# Run with coverage
npm run test:coverage

# Run specific test file
npm test -- user.service.test.ts

# Watch mode
npm test -- --watch
```

## Deployment

```bash
# Build for production
npm run build

# Docker deployment
docker-compose up -d

# Kubernetes deployment
kubectl apply -f k8s/
```

## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'feat: add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

See our [Contributing Guide](CONTRIBUTING.md) for details.

## License

MIT License - see [LICENSE](LICENSE) for details.
```

### Code Documentation

```typescript
// ✅ GOOD: JSDoc for public APIs
/**
 * Creates a new user account and sends welcome email.
 * 
 * @param data - User registration data
 * @param data.email - User's email address (must be unique)
 * @param data.password - User's password (min 8 characters)
 * @param data.name - User's display name
 * @returns The created user object
 * 
 * @throws {ConflictError} If email already exists
 * @throws {ValidationError} If input data is invalid
 * 
 * @example
 * ```typescript
 * const user = await userService.createUser({
 *   email: 'user@example.com',
 *   password: 'securepass123',
 *   name: 'John Doe'
 * });
 * ```
 */
async createUser(data: CreateUserDto): Promise<User> {
  // Implementation
}

// ✅ GOOD: Inline comments for complex logic
/**
 * Calculates retry delay using exponential backoff with jitter.
 * Formula: baseDelay * 2^attempt + random(0, jitter)
 */
function calculateRetryDelay(attempt: number, baseDelay: number = 1000): number {
  const exponentialDelay = baseDelay * Math.pow(2, attempt);
  const jitter = Math.random() * 1000;
  return exponentialDelay + jitter;
}

// ❌ BAD: Redundant comments
// Increment counter by 1
counter++;

// Get user from database
const user = await getUserById(id);
```

### API Documentation (OpenAPI/Swagger)

```yaml
# docs/openapi.yaml
openapi: 3.0.3
info:
  title: Project API
  version: 1.0.0
  description: API documentation for Project

paths:
  /api/users:
    post:
      summary: Create a new user
      tags:
        - Users
      requestBody:
        required: true
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/CreateUserRequest'
      responses:
        '201':
          description: User created successfully
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/UserResponse'
        '400':
          description: Invalid input
        '409':
          description: Email already exists

components:
  schemas:
    CreateUserRequest:
      type: object
      required:
        - email
        - password
        - name
      properties:
        email:
          type: string
          format: email
          example: user@example.com
        password:
          type: string
          minLength: 8
          example: securepass123
        name:
          type: string
          minLength: 1
          maxLength: 100
          example: John Doe
    
    UserResponse:
      type: object
      properties:
        id:
          type: string
          format: uuid
        email:
          type: string
          format: email
        name:
          type: string
        createdAt:
          type: string
          format: date-time
```

### CHANGELOG.md

```markdown
# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.2.0] - 2024-01-15

### Added
- JWT token refresh endpoint (#123)
- Rate limiting middleware for API endpoints
- User profile image upload support

### Changed
- Updated Node.js requirement to v18+
- Improved error messages for authentication failures
- Enhanced logging with request IDs

### Fixed
- Memory leak in cache service (#456)
- Incorrect pagination offset calculation
- Timezone handling in date formatting

### Removed
- Deprecated `/api/v1/users` endpoint
- Legacy authentication method

### Security
- Fixed XSS vulnerability in user input sanitization
- Updated dependencies with security patches

## [1.1.0] - 2024-01-01

### Added
- Initial release
- User authentication and authorization
- Basic CRUD operations for users
```

---

## Quick Reference Card

### Do's and Don'ts

| Category | Do ✅ | Don't ❌ |
|----------|-------|----------|
| **Naming** | `getUserById`, `MAX_RETRIES` | `getData`, `max` |
| **Functions** | Single responsibility, <50 lines | Multiple purposes, >100 lines |
| **Errors** | Specific error types with context | Generic `Error`, silent failures |
| **Tests** | Isolated, deterministic, fast | Dependencies on external services |
| **Commits** | `feat(scope): description` | `fixed stuff`, `wip` |
| **Comments** | Why, not what; complex logic | Obvious code explanations |
| **Dependencies** | Explicit injection, clear interfaces | Hidden dependencies, tight coupling |

### Essential Commands

```bash
# Development
npm run dev              # Start development server
npm run build            # Build for production
npm run lint             # Check code style
npm run lint:fix         # Auto-fix linting issues
npm run type-check       # TypeScript validation

# Testing
npm test                 # Run all tests
npm run test:coverage    # Run with coverage report
npm run test:watch       # Watch mode

# Git
git checkout -b feature/description   # Create feature branch
git commit -m "type: message"         # Conventional commit
git rebase main                       # Update with latest
git push --force-with-lease          # Safe force push

# Database
npm run migrate          # Run migrations
npm run migrate:rollback # Rollback last migration
npm run seed             # Seed database
```

---

## Enforcement and Tooling

### Recommended ESLint Configuration

```javascript
// .eslintrc.js
module.exports = {
  parser: '@typescript-eslint/parser',
  plugins: ['@typescript-eslint', 'prettier'],
  extends: [
    'eslint:recommended',
    'plugin:@typescript-eslint/recommended',
    'prettier'
  ],
  rules: {
    'no-console': 'warn',
    'prefer-const': 'error',
    'no-unused-vars': 'off',
    '@typescript-eslint/no-unused-vars': ['error', { argsIgnorePattern: '^_' }],
    '@typescript-eslint/explicit-function-return-type': 'warn',
    'prettier/prettier': 'error'
  }
};
```

### Husky Pre-commit Hook

```bash
#!/bin/sh
# .husky/pre-commit

npm run lint
npm run type-check
npm run test -- --findRelatedTests $(git diff --cached --name-only)
```

### EditorConfig

```ini
# .editorconfig
root = true

[*]
charset = utf-8
end_of_line = lf
insert_final_newline = true
trim_trailing_whitespace = true
indent_style = space
indent_size = 2

[*.md]
trim_trailing_whitespace = false
```

---

*This document is a living guideline. Propose changes through pull requests with clear justification.*
