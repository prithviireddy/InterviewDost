import { BACKEND_URL } from "@/lib/config";
import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router";
import { Bot, Loader2, PhoneOff, User } from "lucide-react";
import { Button } from "./ui/button";
import { VoiceOrb } from "./VoiceOrb";
import { useAuth } from "@/lib/auth";
import { toast } from "sonner";

type Status = "connecting" | "live" | "ending";

function createLevelMeter(ctx: AudioContext, stream: MediaStream) {
  try {
    const source = ctx.createMediaStreamSource(stream);
    const analyser = ctx.createAnalyser();
    analyser.fftSize = 512;
    analyser.smoothingTimeConstant = 0.8;
    source.connect(analyser);
    const data = new Uint8Array(analyser.fftSize);

    return () => {
      try {
        analyser.getByteTimeDomainData(data);
        let sum = 0;
        for (let i = 0; i < data.length; i++) {
          const v = (data[i]! - 128) / 128;
          sum += v * v;
        }
        const rms = Math.sqrt(sum / data.length);
        return Math.min(1, rms * 3.2);
      } catch {
        return 0;
      }
    };
  } catch {
    return () => 0;
  }
}

async function readMessageData(event: MessageEvent): Promise<string> {
  if (typeof event.data === "string") return event.data;
  if (event.data instanceof Blob) return event.data.text();
  if (event.data instanceof ArrayBuffer) return new TextDecoder().decode(event.data);
  return String(event.data);
}

function createMediaRecorder(stream: MediaStream): MediaRecorder {
  const mimeTypes = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg"];
  for (const mime of mimeTypes) {
    if (MediaRecorder.isTypeSupported(mime)) {
      try {
        return new MediaRecorder(stream, { mimeType: mime });
      } catch {}
    }
  }
  return new MediaRecorder(stream);
}

export function Interview() {
  const { interviewId } = useParams();
  const navigate = useNavigate();
  const { token } = useAuth();

  const [status, setStatus] = useState<Status>("connecting");
  const [aiLevel, setAiLevel] = useState(0);
  const [userLevel, setUserLevel] = useState(0);

  const backendWsRef = useRef<WebSocket | null>(null);
  const deepgramWsRef = useRef<WebSocket | null>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const userStreamRef = useRef<MediaStream | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const rafRef = useRef<number | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const processingRef = useRef(false);

  const speakText = useCallback(async (text: string) => {
    processingRef.current = true;
    setAiLevel(0.5);
    try {
      const res = await fetch(`${BACKEND_URL}/api/v1/tts`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      });
      if (!res.ok) {
        processingRef.current = false;
        setAiLevel(0);
        return;
      }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);

      const audio = new Audio(url);
      audioRef.current = audio;
      const finish = () => {
        setAiLevel(0);
        processingRef.current = false;
        URL.revokeObjectURL(url);
        audioRef.current = null;
      };
      audio.onended = finish;
      audio.onerror = finish;
      audio.play().catch(finish);
    } catch {
      setAiLevel(0);
      processingRef.current = false;
    }
  }, []);

  const speakTextRef = useRef(speakText);
  speakTextRef.current = speakText;

  useEffect(() => {
    if (!token || !interviewId) return;

    let active = true;
    let silenceTimer: any = null;
    let utteranceBuffer = "";

    (async () => {
      try {
        const ms = await navigator.mediaDevices.getUserMedia({ audio: true });
        if (!active) {
          ms.getTracks().forEach((t) => t.stop());
          return;
        }
        userStreamRef.current = ms;

        let audioCtx: AudioContext | null = null;
        try {
          audioCtx = new AudioContext();
          audioCtxRef.current = audioCtx;
        } catch (err) {
          console.warn("AudioContext init error:", err);
        }

        const userMeter = audioCtx ? createLevelMeter(audioCtx, ms) : () => 0;

        // 1. Connect STT WebSocket (Deepgram proxy)
        const wsUrl = BACKEND_URL.replace(/^http/, "ws");
        const dgWs = new WebSocket(`${wsUrl}/api/v1/stt`);
        deepgramWsRef.current = dgWs;

        const sendBufferedUtterance = () => {
          const toSend = utteranceBuffer.trim();
          if (toSend.length > 0 && !processingRef.current) {
            processingRef.current = true;
            utteranceBuffer = "";
            if (backendWsRef.current?.readyState === WebSocket.OPEN) {
              backendWsRef.current.send(
                JSON.stringify({ type: "user_message", text: toSend }),
              );
            }
          }
        };

        dgWs.onmessage = async (firstMsg) => {
          try {
            const text = await readMessageData(firstMsg);
            const parsed = JSON.parse(text);
            if (parsed.type === "connected") {
              dgWs.onmessage = handleSttMessage;

              try {
                const mediaRecorder = createMediaRecorder(ms);
                recorderRef.current = mediaRecorder;
                mediaRecorder.start(250);
                mediaRecorder.addEventListener("dataavailable", (event) => {
                  if (dgWs.readyState === WebSocket.OPEN && event.data.size > 0) {
                    dgWs.send(event.data);
                  }
                });
              } catch (recErr) {
                console.error("MediaRecorder start error:", recErr);
              }
            }
          } catch (e) {
            console.error("STT init error:", e);
          }
        };

        async function handleSttMessage(message: MessageEvent) {
          try {
            const text = await readMessageData(message);
            const received = JSON.parse(text);
            const transcript = received.channel?.alternatives[0]?.transcript;

            if (transcript && typeof transcript === "string") {
              const clean = transcript.trim();
              if (clean.length > 0) {
                if (received.is_final) {
                  utteranceBuffer = (utteranceBuffer + " " + clean).trim();
                }

                // Reset silence fallback timer
                if (silenceTimer) clearTimeout(silenceTimer);
                silenceTimer = setTimeout(() => {
                  sendBufferedUtterance();
                }, 1400);
              }
            }

            // If Deepgram endpointing signals speech finished
            if (received.speech_final) {
              if (silenceTimer) clearTimeout(silenceTimer);
              sendBufferedUtterance();
            }
          } catch (e) {
            console.error("STT message error:", e);
          }
        }

        // 2. Connect Backend WebSocket
        const backendWsUrl = BACKEND_URL.replace(/^http/, "ws");
        const bWs = new WebSocket(
          `${backendWsUrl}/api/v1/ws?interviewId=${interviewId}&token=${encodeURIComponent(token)}`,
        );
        backendWsRef.current = bWs;

        bWs.onmessage = async (event) => {
          try {
            const text = await readMessageData(event);
            const msg = JSON.parse(text);
            if (msg.type === "ai_message" && msg.text) {
              speakTextRef.current(msg.text);
            } else if (msg.type === "error") {
              processingRef.current = false;
            }
          } catch (e) {
            console.error("Backend WS error:", e);
          }
        };

        bWs.onopen = () => {
          if (active) setStatus("live");
        };

        bWs.onerror = (e) => {
          console.error("Backend WS error:", e);
        };

        const tick = () => {
          if (userMeter) setUserLevel(userMeter());
          rafRef.current = requestAnimationFrame(tick);
        };
        rafRef.current = requestAnimationFrame(tick);
      } catch (e) {
        console.error("Mic access denied or unavailable:", e);
        toast.error("Microphone access is required for the interview.");
      }
    })();

    return () => {
      active = false;
      if (silenceTimer) clearTimeout(silenceTimer);
      cleanup();
    };
  }, [interviewId, token]);

  function cleanup() {
    if (rafRef.current) cancelAnimationFrame(rafRef.current);
    audioRef.current?.pause();
    audioRef.current = null;
    if (recorderRef.current && recorderRef.current.state !== "inactive") {
      try {
        recorderRef.current.stop();
      } catch {}
    }
    try {
      deepgramWsRef.current?.close();
    } catch {}
    try {
      backendWsRef.current?.close();
    } catch {}
    userStreamRef.current?.getTracks().forEach((t) => t.stop());
    audioCtxRef.current?.close().catch(() => {});
  }

  function endInterview() {
    setStatus("ending");
    cleanup();
    navigate(`/result/${interviewId}`);
  }

  const aiSpeaking = aiLevel > 0.04;
  const userSpeaking = userLevel > 0.06 && userLevel > aiLevel;

  return (
    <main className="flex h-screen w-screen flex-col overflow-hidden bg-background">
      <header className="flex items-center justify-between px-6 py-5">
        <div className="flex items-center gap-2 text-sm font-medium">
          <span className="relative flex size-2.5">
            <span
              className={
                status === "live"
                  ? "absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-75"
                  : "hidden"
              }
            />
            <span
              className={
                "relative inline-flex size-2.5 rounded-full " +
                (status === "live" ? "bg-emerald-400" : "bg-amber-400")
              }
            />
          </span>
          {status === "connecting"
            ? "Connecting\u2026"
            : status === "ending"
              ? "Wrapping up\u2026"
              : "Interview live"}
        </div>
        <span className="text-sm text-muted-foreground">AI Interview</span>
      </header>

      <div className="flex flex-1 items-center justify-center px-6">
        {status === "connecting" ? (
          <div className="flex flex-col items-center gap-3 text-muted-foreground">
            <Loader2 className="size-7 animate-spin" />
            <p className="text-sm">
              Setting up your interview & microphone\u2026
            </p>
          </div>
        ) : (
          <div className="flex w-full max-w-3xl items-center justify-center gap-12 sm:gap-24">
            <VoiceOrb
              level={aiLevel}
              speaking={aiSpeaking}
              label="Interviewer"
              sublabel="Listening"
              icon={Bot}
              accent="violet"
            />
            <VoiceOrb
              level={userLevel}
              speaking={userSpeaking}
              label="You"
              sublabel="Mic on"
              icon={User}
              accent="emerald"
            />
          </div>
        )}
      </div>

      <footer className="flex justify-center px-6 py-8">
        <Button
          variant="destructive"
          size="lg"
          onClick={endInterview}
          disabled={status === "ending"}
          className="gap-2 rounded-full px-6"
        >
          {status === "ending" ? (
            <Loader2 className="size-4 animate-spin" />
          ) : (
            <PhoneOff className="size-4" />
          )}
          End interview
        </Button>
      </footer>
    </main>
  );
}
