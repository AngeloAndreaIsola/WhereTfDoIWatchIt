"use client";

import { useState } from "react";

interface VpnWatchModalProps {
  isOpen: boolean;
  onClose: () => void;
  requiredCountry: string; // e.g. "JP"
  serviceName: string; // e.g. "Netflix"
  watchUrl: string;
}

export function VpnWatchModal({
  isOpen,
  onClose,
  requiredCountry,
  serviceName,
  watchUrl,
}: VpnWatchModalProps) {
  const [checking, setChecking] = useState(false);
  const [detectedCountry, setDetectedCountry] = useState<string | null>(null);
  const [verified, setVerified] = useState<boolean | null>(null);

  if (!isOpen) return null;

  const runGeoCheck = async () => {
    setChecking(true);
    try {
      const res = await fetch("/api/geo/ip/");
      if (res.ok) {
        const data = await res.json();
        const country = data.country;
        setDetectedCountry(country);
        if (country === requiredCountry) {
          setVerified(true);
        } else {
          setVerified(false);
        }
      } else {
        setVerified(false);
      }
    } catch {
      setVerified(false);
    } finally {
      setChecking(false);
    }
  };

  const handleWatchClick = () => {
    if (watchUrl) {
      window.open(watchUrl, "_blank", "noopener,noreferrer");
      onClose();
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4">
      <div className="bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 rounded-2xl max-w-md w-full p-6 shadow-xl space-y-4">
        <div className="flex items-center justify-between border-b border-gray-100 dark:border-gray-800 pb-3">
          <h3 className="text-lg font-bold text-gray-900 dark:text-gray-100">
            Region & VPN Check
          </h3>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 text-xl font-bold"
          >
            ×
          </button>
        </div>

        <div className="space-y-3">
          <p className="text-sm text-gray-600 dark:text-gray-300">
            This title requires availability in{" "}
            <span className="font-bold text-indigo-600 dark:text-indigo-400">
              {requiredCountry} ({serviceName})
            </span>
            .
          </p>

          {verified === null && !checking && (
            <div className="bg-amber-50 dark:bg-amber-900/30 border border-amber-200 dark:border-amber-800 p-3 rounded-lg text-xs text-amber-800 dark:text-amber-200">
              ⚠️ Connect to your <span className="font-bold">{requiredCountry}</span> VPN before continuing.
            </div>
          )}

          {checking && (
            <div className="text-sm text-gray-500 animate-pulse flex items-center gap-2">
              Verifying current IP country...
            </div>
          )}

          {verified === true && (
            <div className="bg-emerald-50 dark:bg-emerald-900/30 border border-emerald-200 dark:border-emerald-800 p-3 rounded-lg text-xs text-emerald-800 dark:text-emerald-200 flex items-center gap-2">
              ✅ Region verified ({detectedCountry})
            </div>
          )}

          {verified === false && (
            <div className="bg-amber-50 dark:bg-amber-900/30 border border-amber-200 dark:border-amber-800 p-3 rounded-lg text-xs text-amber-800 dark:text-amber-200 space-y-1">
              <p className="font-bold">⚠️ Region mismatch detected</p>
              <p>
                Required region: {requiredCountry} | Current detected:{" "}
                {detectedCountry || "Unknown"}
              </p>
            </div>
          )}
        </div>

        <div className="flex flex-col sm:flex-row gap-2 pt-2">
          <button
            type="button"
            onClick={runGeoCheck}
            disabled={checking}
            className="flex-1 px-4 py-2 text-sm font-medium rounded-lg border border-gray-300 dark:border-gray-700 bg-gray-50 dark:bg-gray-800 text-gray-800 dark:text-gray-200 hover:bg-gray-100"
          >
            Check again
          </button>
          <button
            type="button"
            onClick={handleWatchClick}
            className="flex-1 px-4 py-2 text-sm font-semibold rounded-lg bg-indigo-600 hover:bg-indigo-700 text-white transition-colors"
          >
            {verified === true ? "Watch Now" : "Continue anyway"}
          </button>
        </div>
      </div>
    </div>
  );
}
