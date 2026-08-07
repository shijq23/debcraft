# Requirements Document

## Introduction

Client-side rate limiting for the debcraft mirror client. When downloading large numbers of packages concurrently from a CDN-backed apt repository (CloudFront), the CDN returns HTTP 403 responses to rate-limited requests. This feature adds configurable request-per-second throttling and treats CDN 403 responses as retriable rate-limit signals rather than permanent client errors, dramatically reducing download failures during bulk mirror operations.

## Glossary

- **Rate_Limiter**: A component that controls the rate of outgoing HTTP requests by enforcing a maximum number of requests per second using a token bucket algorithm.
- **Download_Coordinator**: The existing component (`DownloadCoordinator`) that manages concurrent HTTP downloads with retry, exponential backoff, SHA256 verification, and atomic file writes.
- **Mirror_Config**: The top-level configuration dataclass (`MirrorConfig`) holding all mirror settings including connection limits, timeouts, and rate-limit parameters.
- **Config_Reader**: The component that reads and validates `mirrors.toml` configuration files.
- **Token_Bucket**: A rate-limiting algorithm where tokens are added at a fixed rate and each request consumes one token. Requests wait when no tokens are available.
- **CDN_Rate_Limit_Response**: An HTTP 403 response from a CDN (such as CloudFront) that indicates the client has been rate-limited, as opposed to a genuine access-denied error.
- **Backoff_Delay**: The increasing wait time between retry attempts, computed using exponential backoff with jitter.

## Requirements

### Requirement 1: Token Bucket Rate Limiter

**User Story:** As a mirror operator, I want the download client to limit outgoing requests to a configurable rate per second, so that the CDN does not reject requests due to excessive request volume.

#### Acceptance Criteria

1. THE Rate_Limiter SHALL enforce a maximum request rate specified by the configured requests-per-second value using a token bucket algorithm.
2. WHEN a download request is initiated and no token is available, THE Rate_Limiter SHALL delay the request until a token becomes available or until 60 seconds have elapsed, whichever comes first.
3. IF a request has waited 60 seconds without acquiring a token, THEN THE Rate_Limiter SHALL reject the request with a timeout error.
4. WHILE the token bucket contains one or more available tokens, THE Rate_Limiter SHALL permit requests within 1 millisecond of acquisition.
5. THE Rate_Limiter SHALL replenish tokens at a constant rate equal to the configured requests-per-second value, up to a maximum token count equal to the configured burst-size.
6. WHEN the Rate_Limiter is initialized, THE Rate_Limiter SHALL start with a token count equal to the configured burst-size.

### Requirement 2: Rate Limit Configuration

**User Story:** As a mirror operator, I want to configure rate-limiting parameters in `mirrors.toml`, so that I can tune throughput to match CDN limits without modifying code.

#### Acceptance Criteria

1. THE Config_Reader SHALL parse a `rate_limit_rps` field from the `[settings]` section of `mirrors.toml` as a numeric value (integer or decimal) representing maximum requests per second, accepting values from 1 to 1000 inclusive.
2. THE Config_Reader SHALL parse a `rate_limit_burst` field from the `[settings]` section of `mirrors.toml` as a positive integer representing the maximum burst size, accepting values from 1 to 200 inclusive.
3. WHEN `rate_limit_rps` is not specified in `mirrors.toml`, THE Config_Reader SHALL use a default value of 50 requests per second.
4. WHEN `rate_limit_burst` is not specified in `mirrors.toml`, THE Config_Reader SHALL use a default value equal to the configured `max_connections_per_repo` value.
5. IF `rate_limit_rps` is less than 1 or greater than 1000, THEN THE Config_Reader SHALL reject the configuration and report a validation error indicating the accepted range.
6. IF `rate_limit_burst` is less than 1 or greater than 200, THEN THE Config_Reader SHALL reject the configuration and report a validation error indicating the accepted range.
7. IF `rate_limit_rps` or `rate_limit_burst` is present but contains a non-numeric value, THEN THE Config_Reader SHALL reject the configuration and report a validation error indicating the expected type.

### Requirement 3: CDN 403 Retry Handling

**User Story:** As a mirror operator, I want HTTP 403 responses from the CDN to be retried with backoff, so that transient rate-limiting does not permanently fail package downloads.

#### Acceptance Criteria

1. WHEN an HTTP 403 response is received during a download, THE Download_Coordinator SHALL classify the response as a retriable error and proceed to the retry loop instead of failing immediately.
2. WHEN a retriable 403 response is received, THE Download_Coordinator SHALL wait for a duration computed as exponential backoff with random jitter (jitter range: 0 to 25% of the computed delay) before retrying the request.
3. THE Download_Coordinator SHALL retry 403 responses up to a maximum of 3 total attempts (1 initial attempt plus 2 retries), the same limit used for 5xx server errors.
4. WHEN all 3 attempts for a 403 response are exhausted without success, THE Download_Coordinator SHALL return a failed DownloadResult containing the HTTP 403 status code, an error message describing the rate-limit failure, and a retry count equal to the number of retries performed.
5. WHEN a 403 retry succeeds on a subsequent attempt, THE Download_Coordinator SHALL return a successful DownloadResult with the retry_count field set to the zero-indexed attempt number on which success occurred.
6. WHEN a 403 response triggers a retry, THE Download_Coordinator SHALL clean up any partial download file (.part file) created during the failed attempt before waiting and retrying.

### Requirement 4: Rate Limiter Integration with Download Pipeline

**User Story:** As a mirror operator, I want rate limiting applied to all download requests in the batch pipeline, so that concurrent downloads are collectively throttled regardless of concurrency level.

#### Acceptance Criteria

1. THE Download_Coordinator SHALL acquire a token from the Rate_Limiter before initiating each HTTP request, including initial attempts and retry attempts.
2. WHILE the Rate_Limiter is throttling requests, THE Download_Coordinator SHALL maintain active downloads already in progress without interruption — only new outgoing requests are delayed.
3. THE Rate_Limiter SHALL apply globally across all concurrent download tasks within a single batch operation, such that the aggregate request rate does not exceed the configured limit.
4. WHEN rate limiting is configured, THE Download_Coordinator SHALL log at DEBUG level the effective rate-limit settings (requests-per-second and burst-size) at the start of each batch download.
5. WHEN the Download_Coordinator is closed or a batch operation is cancelled, THE Rate_Limiter SHALL release any tasks waiting to acquire a token.

### Requirement 5: Backoff Scaling for Rate-Limited Responses

**User Story:** As a mirror operator, I want longer backoff delays for rate-limited (403) responses compared to server errors, so that the client gives the CDN adequate recovery time.

#### Acceptance Criteria

1. WHEN computing backoff for a 403 rate-limit response, THE Download_Coordinator SHALL use a base backoff delay of 5 seconds.
2. WHEN computing backoff for a 403 rate-limit response, THE Download_Coordinator SHALL use a maximum backoff delay of 60 seconds.
3. WHEN computing backoff for a successive 403 retry attempt, THE Download_Coordinator SHALL multiply the base delay by 2 raised to the power of the attempt number (starting from 0), then add a random jitter value between 0 and 50% of the computed delay, capping the total at the maximum backoff delay.
4. WHEN computing backoff for a 5xx server error or network error, THE Download_Coordinator SHALL use the existing backoff parameters (1 second base, 30 seconds maximum) with the same exponential growth and jitter formula.
