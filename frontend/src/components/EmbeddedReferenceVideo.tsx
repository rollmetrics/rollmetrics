import { useEffect, useRef } from "react";

export interface ParsedReferenceMedia {
  kind: "youtube" | "video" | "link";
  embedUrl?: string;
  videoUrl?: string;
  startSec: number;
  originalUrl: string;
}

export function parseTimestampParam(raw: string | null): number {
  if (!raw) return 0;
  const trimmed = raw.trim().toLowerCase();
  if (/^\d+$/.test(trimmed)) return Math.max(0, parseInt(trimmed, 10));
  const match = trimmed.match(/^(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s?)?$/);
  if (!match) return 0;
  const hours = match[1] ? parseInt(match[1], 10) : 0;
  const mins = match[2] ? parseInt(match[2], 10) : 0;
  const secs = match[3] ? parseInt(match[3], 10) : 0;
  return Math.max(0, hours * 3600 + mins * 60 + secs);
}

export function parseReferenceMedia(url: string, explicitStartSec?: number): ParsedReferenceMedia {
  const clean = (url || "").trim();
  const baseStart = typeof explicitStartSec === "number" && explicitStartSec > 0 ? Math.floor(explicitStartSec) : 0;
  if (!clean) {
    return { kind: "link", startSec: baseStart, originalUrl: "" };
  }

  let parsed: URL | null = null;
  try {
    parsed = new URL(clean, typeof window !== "undefined" ? window.location.origin : "http://127.0.0.1:8090");
  } catch {
    parsed = null;
  }

  if (parsed) {
    const host = parsed.hostname.toLowerCase();
    const path = parsed.pathname;
    let ytId = "";

    if (host === "youtu.be") {
      ytId = path.replace(/^\/+/, "").split("/")[0] || "";
    } else if (host.endsWith("youtube.com") || host.endsWith("youtube-nocookie.com")) {
      if (path === "/watch") {
        ytId = parsed.searchParams.get("v") || "";
      } else if (path.startsWith("/embed/") || path.startsWith("/shorts/") || path.startsWith("/live/")) {
        ytId = path.split("/")[2] || "";
      }
    }

    if (ytId && /^[A-Za-z0-9_-]{6,20}$/.test(ytId)) {
      const urlStart = parseTimestampParam(parsed.searchParams.get("t") || parsed.searchParams.get("start"));
      const startSec = baseStart > 0 ? baseStart : urlStart;
      const params = new URLSearchParams({ rel: "0", modestbranding: "1" });
      if (startSec > 0) params.set("start", String(startSec));
      return {
        kind: "youtube",
        embedUrl: `https://www.youtube-nocookie.com/embed/${ytId}?${params.toString()}`,
        startSec,
        originalUrl: clean,
      };
    }

    const lowerPath = path.toLowerCase();
    const isDirectVideo =
      /\.(mp4|mov|webm|m4v)$/.test(lowerPath) ||
      lowerPath.startsWith("/api/academies/") ||
      lowerPath.startsWith("/api/media/") ||
      (lowerPath.startsWith("/api/matches/") && lowerPath.endsWith("/video"));
    if (isDirectVideo) {
      return {
        kind: "video",
        videoUrl: clean,
        startSec: baseStart,
        originalUrl: clean,
      };
    }
  }

  return {
    kind: "link",
    startSec: baseStart,
    originalUrl: clean,
  };
}

interface EmbeddedReferenceVideoProps {
  url: string;
  title: string;
  startSec?: number;
  resolveVideoUrl?: (rawUrl: string) => string;
  testId?: string;
}

export function EmbeddedReferenceVideo({
  url,
  title,
  startSec,
  resolveVideoUrl,
  testId = "embedded-reference-video",
}: EmbeddedReferenceVideoProps) {
  const media = parseReferenceMedia(url, startSec);
  const videoRef = useRef<HTMLVideoElement | null>(null);

  useEffect(() => {
    const el = videoRef.current;
    if (!el || media.kind !== "video" || media.startSec <= 0) return;
    const seek = () => {
      try {
        el.currentTime = media.startSec;
      } catch {
        // Ignore seek errors on unloaded metadata
      }
    };
    el.addEventListener("loadedmetadata", seek, { once: true });
    return () => el.removeEventListener("loadedmetadata", seek);
  }, [media.kind, media.startSec, media.videoUrl]);

  if (media.kind === "youtube" && media.embedUrl) {
    return (
      <div className="embed-video-wrap" data-testid={testId} data-media-kind="youtube">
        <div className="embed-video-frame">
          <iframe
            className="embed-video-iframe"
            src={media.embedUrl}
            title={title}
            loading="lazy"
            allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
            allowFullScreen
          />
        </div>
        <div className="embed-video-caption">
          <span className="embed-video-title">{title}</span>
          <a href={media.originalUrl} target="_blank" rel="noreferrer" className="embed-source-link">
            Open source -&gt;
          </a>
        </div>
      </div>
    );
  }

  if (media.kind === "video" && media.videoUrl) {
    const src = resolveVideoUrl ? resolveVideoUrl(media.videoUrl) : media.videoUrl;
    return (
      <div className="embed-video-wrap" data-testid={testId} data-media-kind="video">
        <div className="embed-video-frame">
          <video
            ref={videoRef}
            className="embed-video-player"
            src={src}
            controls
            playsInline
            preload="metadata"
          />
        </div>
        <div className="embed-video-caption">
          <span className="embed-video-title">{title}</span>
          <a href={src} target="_blank" rel="noreferrer" className="embed-source-link">
            Open clip -&gt;
          </a>
        </div>
      </div>
    );
  }

  return (
    <div className="embed-link-fallback" data-testid={testId} data-media-kind="link">
      <a href={media.originalUrl} target="_blank" rel="noreferrer" className="cta-jump">
        {title || "Open reference"} -&gt;
      </a>
    </div>
  );
}
