/**
 * Input validation utilities for security and data integrity
 */

/**
 * Validates and sanitizes search query input
 * @param query - The raw search query from user input
 * @returns Sanitized query string
 * @throws Error if query is invalid
 */
export function validateSearchQuery(query: string): string {
  // Check if query is a string
  if (typeof query !== 'string') {
    throw new Error('Invalid query type');
  }

  // Trim whitespace
  const trimmed = query.trim();

  // Check length constraints (prevent DoS with very long queries)
  const MAX_QUERY_LENGTH = 200;
  if (trimmed.length > MAX_QUERY_LENGTH) {
    throw new Error(`Query too long. Maximum length is ${MAX_QUERY_LENGTH} characters.`);
  }

  // Allow empty queries (returns empty results)
  if (trimmed.length === 0) {
    return '';
  }

  // Minimum length check
  const MIN_QUERY_LENGTH = 2;
  if (trimmed.length < MIN_QUERY_LENGTH) {
    throw new Error(`Query too short. Minimum length is ${MIN_QUERY_LENGTH} characters.`);
  }

  // Remove any control characters that could cause issues
  const sanitized = trimmed.replace(/[\x00-\x1F\x7F]/g, '');

  return sanitized;
}

/**
 * Validates a URL to ensure it's from a trusted domain
 * @param url - The URL to validate
 * @param allowedDomains - List of allowed domains
 * @returns true if URL is valid and from allowed domain
 */
export function isValidUrl(url: string | null, allowedDomains: string[] = []): boolean {
  if (!url) {
    return false;
  }

  try {
    const urlObj = new URL(url);
    
    // Only allow HTTPS URLs
    if (urlObj.protocol !== 'https:') {
      return false;
    }

    // If allowed domains specified, check if URL is from one of them
    if (allowedDomains.length > 0) {
      const isAllowed = allowedDomains.some(domain => 
        urlObj.hostname === domain || urlObj.hostname.endsWith(`.${domain}`)
      );
      return isAllowed;
    }

    return true;
  } catch {
    return false;
  }
}
