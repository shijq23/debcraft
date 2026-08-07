# Bugfix Requirements Document

## Introduction

The `TokenBucketRateLimiter` class in `rate_limiter.py` is fully implemented but never instantiated or invoked anywhere in the download pipeline. The `DownloadCoordinator` sends HTTP requests without any rate throttling, and `engine.py` hardcodes `_MAX_CONCURRENT_DOWNLOADS = 20` instead of using the user's `max_connections_per_repo` config value. As a result, users who configure `rate_limit_rps`, `rate_limit_burst`, and `max_connections_per_repo` in `mirrors.toml` still experience CDN HTTP 403 rate-limiting errors because the settings have no effect on actual request behavior.

## Bug Analysis

### Current Behavior (Defect)

1.1 WHEN `rate_limit_rps` and `rate_limit_burst` are configured in `mirrors.toml` THEN the system parses and stores the values in `MirrorConfig` but never instantiates `TokenBucketRateLimiter`, resulting in no request-rate throttling

1.2 WHEN `DownloadCoordinator._attempt_download()` sends an HTTP GET request THEN the system does not call any rate-limiting mechanism before issuing the request, allowing unbounded request rates

1.3 WHEN `DownloadCoordinator.check_conditional()` sends an HTTP HEAD request THEN the system does not call any rate-limiting mechanism before issuing the request, allowing unbounded HEAD request rates

1.4 WHEN `max_connections_per_repo` is configured in `mirrors.toml` THEN `engine.py` ignores the configured value and uses a hardcoded `_MAX_CONCURRENT_DOWNLOADS = 20` for batch download concurrency

1.5 WHEN the CDN receives requests faster than its rate limit allows THEN the system receives HTTP 403 responses and fails downloads that would succeed under proper throttling

### Expected Behavior (Correct)

2.1 WHEN `rate_limit_rps` and `rate_limit_burst` are configured in `mirrors.toml` THEN the system SHALL instantiate a `TokenBucketRateLimiter` with those values and use it to throttle all outgoing HTTP requests

2.2 WHEN `DownloadCoordinator._attempt_download()` is about to send an HTTP GET request THEN the system SHALL call `TokenBucketRateLimiter.acquire()` before issuing the request, blocking until a token is available

2.3 WHEN `DownloadCoordinator.check_conditional()` is about to send an HTTP HEAD request THEN the system SHALL call `TokenBucketRateLimiter.acquire()` before issuing the request, blocking until a token is available

2.4 WHEN `max_connections_per_repo` is configured in `mirrors.toml` THEN the system SHALL use the configured value as the `max_concurrent` parameter for batch downloads instead of the hardcoded constant

2.5 WHEN the rate limiter is active THEN the system SHALL limit outgoing HTTP requests to no more than `rate_limit_rps` requests per second (sustained), with short bursts up to `rate_limit_burst`

### Unchanged Behavior (Regression Prevention)

3.1 WHEN `rate_limit_rps` and `rate_limit_burst` are not explicitly configured THEN the system SHALL CONTINUE TO use the default values (50.0 rps, burst = max_connections_per_repo) and throttle accordingly

3.2 WHEN a download fails with a 5xx or network error THEN the system SHALL CONTINUE TO retry with exponential backoff up to `_MAX_ATTEMPTS` times

3.3 WHEN a download fails with a 4xx error (other than rate-limiting 403) THEN the system SHALL CONTINUE TO fail immediately without retry

3.4 WHEN `max_total_connections` is configured THEN the system SHALL CONTINUE TO respect the `aiohttp.TCPConnector` connection pool limit for total concurrent connections

3.5 WHEN SHA256 or size verification fails after download THEN the system SHALL CONTINUE TO reject the file and retry

3.6 WHEN the download coordinator session is closed THEN the system SHALL CONTINUE TO clean up the aiohttp session and connector properly

---

## Bug Condition (Formal)

```pascal
FUNCTION isBugCondition(X)
  INPUT: X of type DownloadRequest
  OUTPUT: boolean

  // The bug manifests for ALL download requests because the rate limiter
  // is never integrated — every request goes unthrottled regardless of config.
  // The observable failure occurs when request rate exceeds CDN tolerance.
  RETURN X.config.rate_limit_rps IS configured AND TokenBucketRateLimiter IS NOT instantiated
END FUNCTION
```

```pascal
// Property: Fix Checking — Rate Limiter Integration
FOR ALL X WHERE isBugCondition(X) DO
  result ← DownloadCoordinator'._attempt_download(X)
  ASSERT rate_limiter.acquire() was called before HTTP request
  ASSERT effective_request_rate <= X.config.rate_limit_rps (sustained)
END FOR
```

```pascal
// Property: Fix Checking — Concurrency Respects Config
FOR ALL X WHERE X.config.max_connections_per_repo != 20 DO
  result ← engine'.download_batch(tasks, X.config)
  ASSERT batch_concurrency == X.config.max_connections_per_repo
END FOR
```

```pascal
// Property: Preservation Checking
FOR ALL X WHERE NOT isBugCondition(X) DO
  // For non-rate-limited behavior (retry logic, checksum verification,
  // connection pooling, atomic writes), the fixed code behaves identically.
  ASSERT F(X) = F'(X)
END FOR
```
