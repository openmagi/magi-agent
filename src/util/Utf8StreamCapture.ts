import { StringDecoder } from "node:string_decoder";

export class Utf8StreamCapture {
  private readonly decoder = new StringDecoder("utf8");
  private ended = false;
  public text = "";
  public truncated = false;

  constructor(private readonly maxChars = Number.POSITIVE_INFINITY) {}

  write(chunk: Buffer | Uint8Array | string): void {
    if (this.ended) return;
    const piece = typeof chunk === "string" ? chunk : this.decoder.write(Buffer.from(chunk));
    this.append(piece);
  }

  end(): string {
    if (this.ended) return this.text;
    this.ended = true;
    this.append(this.decoder.end());
    return this.text;
  }

  private append(piece: string): void {
    if (!piece) return;
    const room = this.maxChars - this.text.length;
    if (room <= 0) {
      this.truncated = true;
      return;
    }
    this.text += piece.slice(0, room);
    if (piece.length > room) this.truncated = true;
  }
}
