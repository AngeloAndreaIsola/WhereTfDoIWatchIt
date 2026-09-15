"use client";

import { createContext, useCallback, useContext, useEffect, useState } from "react";

interface CurrentLocationContextValue {
  currentCountry: string;
  loading: boolean;
  error: string | null;
  setCurrentCountry: (country: string) => Promise<void>;
}

const CurrentLocationContext = createContext<CurrentLocationContextValue>({
  currentCountry: "",
  loading: true,
  error: null,
  setCurrentCountry: async () => {},
});

export function CurrentLocationProvider({ children }: { children: React.ReactNode }) {
  const [currentCountry, setCurrentCountryState] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;

    async function load() {
      try {
        const preferencesResponse = await fetch("/api/preferences/");
        if (!preferencesResponse.ok) throw new Error(`Failed to load preferences (${preferencesResponse.status})`);
        const preferences = await preferencesResponse.json();
        const savedCountry = typeof preferences.current_country === "string" ? preferences.current_country.toUpperCase() : "";

        if (savedCountry) {
          if (active) setCurrentCountryState(savedCountry);
          return;
        }

        const geoResponse = await fetch("/api/geo/ip/");
        const geo = geoResponse.ok ? await geoResponse.json() : null;
        const detectedCountry = typeof geo?.country === "string" ? geo.country.toUpperCase() : "";
        if (!detectedCountry) return;

        const saveResponse = await fetch("/api/preferences/", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ current_country: detectedCountry }),
        });
        if (!saveResponse.ok) throw new Error(`Failed to save current location (${saveResponse.status})`);
        if (active) setCurrentCountryState(detectedCountry);
      } catch (loadError: unknown) {
        if (active) setError(loadError instanceof Error ? loadError.message : "Failed to load current location");
      } finally {
        if (active) setLoading(false);
      }
    }

    load();
    return () => { active = false; };
  }, []);

  const setCurrentCountry = useCallback(async (country: string) => {
    const normalizedCountry = country.trim().toUpperCase();
    const response = await fetch("/api/preferences/", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ current_country: normalizedCountry }),
    });
    if (!response.ok) throw new Error(`Failed to save current location (${response.status})`);
    setCurrentCountryState(normalizedCountry);
    setError(null);
  }, []);

  return (
    <CurrentLocationContext.Provider value={{ currentCountry, loading, error, setCurrentCountry }}>
      {children}
    </CurrentLocationContext.Provider>
  );
}

export function useCurrentLocation() {
  return useContext(CurrentLocationContext);
}
