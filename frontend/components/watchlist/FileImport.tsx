"use client";

import React, { useState } from "react";
import AmbiguousReviewLink from "@/components/AmbiguousReviewLink";
import type {
  AmbiguousMatchItem,
  ResolvedWatchlistRow,
  SkippedImportItem,
} from "@/lib/api";

interface UploadResult {
  resolved: ResolvedWatchlistRow[];
  ambiguous: AmbiguousMatchItem[];
  skipped: SkippedImportItem[];
  watched_count?: number;
  ratings_count?: number;
  reviews_count?: number;
  diary_events_count?: number;
}

export default function FileImport() {
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState("");
  const [uploadResult, setUploadResult] = useState<UploadResult | null>(null);
  const [isDragging, setIsDragging] = useState(false);

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0] ?? null;
    setSelectedFile(file);
    setUploadError("");
  };

  const handleDragOver = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setIsDragging(true);
  };

  const handleDragLeave = () => {
    setIsDragging(false);
  };

  const handleDrop = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setIsDragging(false);
    const file = e.dataTransfer.files?.[0] ?? null;
    if (file) {
      setSelectedFile(file);
      setUploadError("");
    }
  };

  const handleUpload = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedFile) return;

    setUploading(true);
    setUploadError("");
    setUploadResult(null);

    const formData = new FormData();
    formData.append("file", selectedFile);

    try {
      const res = await fetch("/api/watchlist/", {
        method: "POST",
        body: formData,
      });

      if (res.status === 429) {
        const body = await res.json().catch(() => ({}));
        setUploadError(body.error ?? "Rate limited — try again later.");
        return;
      }

      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.error ?? `Upload failed (${res.status})`);
      }

      const data: UploadResult = await res.json();
      setUploadResult(data);
    } catch (err: unknown) {
      setUploadError(
        err instanceof Error ? err.message : "Upload failed"
      );
    } finally {
      setUploading(false);
    }
  };

  return (
    <>
      {/* ─── Import from file ──────────────────────────────────────────────── */}
      <section className="rounded-xl border border-gray-200 bg-white p-6 shadow-xs dark:border-gray-700 dark:bg-gray-800">
        <h2 className="mb-2 text-lg font-semibold text-gray-900 dark:text-gray-100">
          Import Watchlist Export
        </h2>
        <p className="mb-4 text-xs text-gray-500 dark:text-gray-400">
          Upload Letterboxd exports (.csv, .zip) or MyAnimeList exports (.xml,
          .xml.gz, .gz).
        </p>

        <form onSubmit={handleUpload} className="space-y-4">
          <div
            onDragOver={handleDragOver}
            onDragLeave={handleDragLeave}
            onDrop={handleDrop}
            className={`flex flex-col items-center justify-center rounded-xl border-2 border-dashed p-6 text-center transition-colors cursor-pointer ${
              isDragging
                ? "border-blue-500 bg-blue-50 dark:border-blue-400 dark:bg-blue-950/30"
                : "border-gray-300 hover:border-gray-400 dark:border-gray-700 dark:hover:border-gray-600"
            }`}
            onClick={() =>
              document.getElementById("file-upload-input")?.click()
            }
          >
            <svg
              className="mx-auto h-10 w-10 text-gray-400 dark:text-gray-500"
              stroke="currentColor"
              fill="none"
              viewBox="0 0 48 48"
              aria-hidden="true"
            >
              <path
                d="M28 8H12a4 4 0 00-4 4v20m32-12v8m0 0v8a4 4 0 01-4 4H12a4 4 0 01-4-4v-4m32-4l-3.172-3.172a4 4 0 00-5.656 0L28 28M8 32l9.172-9.172a4 4 0 015.656 0L28 28m0 0l4 4m4-24h8m-4-4v8"
                strokeWidth={2}
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
            <p className="mt-2 text-sm text-gray-600 dark:text-gray-300">
              <span className="font-semibold text-blue-600 dark:text-blue-400">
                Click to choose
              </span>{" "}
              or drag & drop your export file here
            </p>
            <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
              Supports .csv, .zip, .xml, .gz, .xml.gz
            </p>
            {selectedFile && (
              <p className="mt-3 rounded-md bg-blue-100 px-3 py-1 text-xs font-semibold text-blue-800 dark:bg-blue-900/50 dark:text-blue-300">
                Selected: {selectedFile.name} (
                {(selectedFile.size / 1024).toFixed(1)} KB)
              </p>
            )}
            <input
              id="file-upload-input"
              type="file"
              accept=".csv,.zip,.xml,.gz,.xml.gz,text/csv,application/zip,application/xml,text/xml,application/gzip,application/x-gzip"
              onChange={handleFileChange}
              className="hidden"
            />
          </div>
          <div className="flex justify-end">
            <button
              type="submit"
              disabled={!selectedFile || uploading}
              className="rounded-lg bg-blue-600 px-5 py-2.5 text-sm font-medium text-white shadow-xs hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-blue-500 dark:hover:bg-blue-600"
            >
              {uploading ? "Importing…" : "Import File"}
            </button>
          </div>
        </form>
        {uploadError && (
          <div className="mt-4 rounded-lg border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-700 dark:bg-red-900/30 dark:text-red-400">
            {uploadError}
          </div>
        )}
      </section>

      {uploadResult && (
        <section className="space-y-4">
          <div className="rounded-xl border border-gray-200 bg-gray-50 p-4 dark:border-gray-700 dark:bg-gray-800/50">
            <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
              Import Summary
            </h3>
            <div className="flex flex-wrap gap-4 text-xs font-medium">
              <span className="text-green-600 dark:text-green-400">
                Resolved Watchlist: {uploadResult.resolved.length}
              </span>
              <span className="text-amber-600 dark:text-amber-400">
                Ambiguous: {uploadResult.ambiguous.length}
              </span>
              <span className="text-gray-500 dark:text-gray-400">
                Skipped: {uploadResult.skipped.length}
              </span>
            </div>
          </div>

          {uploadResult.ambiguous.length > 0 && (
            <div className="flex items-center justify-between rounded-xl border border-amber-300 bg-amber-50 p-4 dark:border-amber-700 dark:bg-amber-900/30">
              <div>
                <h4 className="font-semibold text-amber-800 dark:text-amber-300">
                  {uploadResult.ambiguous.length} Ambiguous Match
                  {uploadResult.ambiguous.length > 1 ? "es" : ""}
                </h4>
                <p className="text-xs text-amber-700 dark:text-amber-400">
                  Some titles could not be automatically matched to TMDb.
                </p>
              </div>

              <AmbiguousReviewLink
                items={uploadResult.ambiguous}
                className="rounded-lg bg-amber-600 px-4 py-2 text-sm font-medium text-white shadow-xs hover:bg-amber-700 dark:bg-amber-500 dark:hover:bg-amber-600"
              >
                Review ambiguous matches
              </AmbiguousReviewLink>
            </div>
          )}
        </section>
      )}
    </>
  );
}
