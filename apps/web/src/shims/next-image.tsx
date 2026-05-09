import type { ImgHTMLAttributes } from "react";

interface ImageProps extends ImgHTMLAttributes<HTMLImageElement> {
  src: string;
  alt: string;
  width?: number;
  height?: number;
  fill?: boolean;
  unoptimized?: boolean;
}

export default function Image({ fill: _fill, unoptimized: _unoptimized, ...props }: ImageProps) {
  return <img {...props} />;
}
