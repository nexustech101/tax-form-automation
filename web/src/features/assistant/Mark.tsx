/** The assistant's mark: a four-point sparkle in the accent colour (decorative). */
export function Mark({ size = 20, className }: { size?: number; className?: string }) {
  return (
    <svg className={className} width={size} height={size} viewBox="0 0 24 24" aria-hidden focusable="false">
      <path fill="var(--accent)" d="M12 1.5C12.5 7.2 16.8 11.5 22.5 12 16.8 12.5 12.5 16.8 12 22.5 11.5 16.8 7.2 12.5 1.5 12 7.2 11.5 11.5 7.2 12 1.5Z" />
    </svg>
  );
}
