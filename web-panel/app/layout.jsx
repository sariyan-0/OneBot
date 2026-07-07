import "./globals.css";

export const metadata = {
  title: "ONEBOT Admin",
  description: "ONEBOT operator panel",
};

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
