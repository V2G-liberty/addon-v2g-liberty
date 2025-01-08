export function elapsedTimeSince(dateTimeStamp: string) {
  const parsedDate = Date.parse(dateTimeStamp);
  if (isNaN(parsedDate)) {
    return ' ?: ?: ?';
  }
  // Use max to prevent negative values
  const seconds = Math.max(Math.floor((Date.now() - parsedDate) / 1000), 0);

  const hours = String(Math.floor(seconds / 3600)).padStart(2, '0');
  const minutes = String(Math.floor((seconds % 3600) / 60)).padStart(2, '0');
  const remainingSeconds = String(seconds % 60).padStart(2, '0');

  return `${hours}:${minutes}:${remainingSeconds}`;
}