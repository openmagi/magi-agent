import Link from "next/link";

export default function NotFound() {
  return (
    <div className="min-h-screen flex items-center justify-center">
      <div className="text-center">
        <h1 className="text-4xl font-bold mb-4">404</h1>
        <p className="text-gray-600 mb-6">Page not found</p>
        <Link href="/" className="px-4 py-2 bg-black text-white rounded-lg">
          Go home
        </Link>
      </div>
    </div>
  );
}
