"use client";

const COUNTRIES = [
  ["AR", "Argentina"], ["AU", "Australia"], ["AT", "Austria"], ["BE", "Belgium"], ["BR", "Brazil"], ["CA", "Canada"], ["CL", "Chile"], ["CO", "Colombia"], ["CZ", "Czechia"], ["DK", "Denmark"], ["FI", "Finland"], ["FR", "France"], ["DE", "Germany"], ["GR", "Greece"], ["HK", "Hong Kong"], ["HU", "Hungary"], ["IN", "India"], ["ID", "Indonesia"], ["IE", "Ireland"], ["IL", "Israel"], ["IT", "Italy"], ["JP", "Japan"], ["MY", "Malaysia"], ["MX", "Mexico"], ["NL", "Netherlands"], ["NZ", "New Zealand"], ["NG", "Nigeria"], ["NO", "Norway"], ["PE", "Peru"], ["PH", "Philippines"], ["PL", "Poland"], ["PT", "Portugal"], ["SA", "Saudi Arabia"], ["SG", "Singapore"], ["ZA", "South Africa"], ["KR", "South Korea"], ["ES", "Spain"], ["SE", "Sweden"], ["CH", "Switzerland"], ["TW", "Taiwan"], ["TH", "Thailand"], ["TR", "Turkey"], ["UA", "Ukraine"], ["AE", "United Arab Emirates"], ["GB", "United Kingdom"], ["US", "United States"],
] as const;

function flagEmoji(country: string): string {
  return country
    .toUpperCase()
    .split("")
    .filter((letter) => letter >= "A" && letter <= "Z")
    .map((letter) => String.fromCodePoint(127397 + letter.charCodeAt(0)))
    .join("");
}

export function countryName(country: string): string {
  return COUNTRIES.find(([code]) => code === country.toUpperCase())?.[1] ?? country.toUpperCase();
}

interface CountrySelectProps {
  country: string;
  onChange: (country: string) => void;
  disabled?: boolean;
}

export default function CountrySelect({ country, onChange, disabled = false }: CountrySelectProps) {
  return (
    <select
      aria-label="Current location"
      value={country}
      disabled={disabled}
      onChange={(event) => onChange(event.target.value)}
      className="w-full px-3 py-1.5 text-sm rounded-lg border border-gray-300 dark:border-gray-700 bg-gray-50 dark:bg-gray-800 text-gray-900 dark:text-gray-100 disabled:cursor-wait disabled:opacity-60"
    >
      {!country && <option value="">Choose a country</option>}
      {COUNTRIES.map(([code, name]) => (
        <option key={code} value={code}>{flagEmoji(code)} {name}</option>
      ))}
      {country && !COUNTRIES.some(([code]) => code === country) && <option value={country}>{flagEmoji(country)} {country}</option>}
    </select>
  );
}
