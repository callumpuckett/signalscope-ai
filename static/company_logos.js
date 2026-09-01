(function () {
    "use strict";

    var providerSymbolOverrides = {
        "0A2V.L": "SAN.PA",
        "0AA7.L": "TRUE-B.ST",
        "0BOE.L": "BCO.DE",
        "0HD6.L": "0RIH.L",
        "0HLE.L": "SAN",
        "0K8D.L": "NOKIA.HE",
        "0NOF.L": "PG",
        "0Q15.L": "ABT",
        "0QOS.L": "PEP",
        "0QYJ.L": "CRM",
        "0QYP.L": "MSFT",
        "0QZ6.L": "NKE.DE",
        "0R1W.L": "WMT",
        "0R37.L": "BRK-B",
        "ALC": "ALC.SW",
        "AXIA-PC": "AXIA",
        "BA": "BCO.DE",
        "BCE": "BCE.TO",
        "CVE": "CVE.TO",
        "ERIC": "ERIC-B.ST",
        "FRMI.L": "FRMI",
        "HEI": "HEI-A",
        "LMT": "LMT.MX",
        "MICC.L": "MICC",
        "NKE": "NKE.DE",
        "NLY": "NLY-PF",
        "NOK": "NOKIA.HE",
        "RACE": "RACE.MI",
        "SNY": "SAN.PA",
        "SOL-USD": "SOLUSD",
        "STLA": "STLAM.MI",
        "TECK": "TECK-B.TO",
        "UBER": "0A1U.L",
        "UBS": "UBSG.SW",
        "UNH": "0R0O.L",
        "V": "0QZ0.L"
    };
    var fallbackTickers = {
        "0A4H.L": true,
        "ABBV": true,
        "ADI": true,
        "ADSK": true,
        "AIG": true,
        "ALB": true,
        "ALL": true,
        "ALNY": true,
        "AMP": true,
        "ANET": true,
        "APG": true,
        "APP": true,
        "AVB": true,
        "AWK": true,
        "AXON": true,
        "BLK": true,
        "BWA": true,
        "CDNS": true,
        "CEG": true,
        "CSX": true,
        "CTAS": true,
        "DHI": true,
        "DIS": true,
        "ET": true,
        "FAST": true,
        "HSY": true,
        "IBM": true,
        "IOT": true,
        "IREN": true,
        "JBL": true,
        "JMGI.L": true,
        "KR": true,
        "MRVL": true,
        "NTAP": true,
        "OKTA": true,
        "ON": true,
        "QQQ": true,
        "RBLX": true,
        "RCL": true,
        "REGN": true,
        "ROKU": true,
        "SMH": true,
        "STT": true,
        "ULTA": true,
        "VRTX": true,
        "WSM": true,
        "ZM": true,
        "^DJI": true,
        "^FTSE": true,
        "^GSPC": true,
        "^HSI": true,
        "^IXIC": true,
        "^N225": true,
        "^RUT": true
    };

    function showInitials(image) {
        image.hidden = true;
        image.removeAttribute("src");
        image.dataset.logoState = "initials";
    }

    function handleLogoError(image) {
        var fallbackSource = image.dataset.logoFallbackSrc || "";
        if (fallbackSource && image.dataset.logoFallbackTried !== "true") {
            image.dataset.logoFallbackTried = "true";
            image.src = fallbackSource;
            return;
        }
        showInitials(image);
    }

    function companyNameForAlt(label, ticker) {
        var companyName = String(label || ticker || "Company").trim();
        var escapedTicker = String(ticker || "").replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
        if (escapedTicker) {
            companyName = companyName.replace(
                new RegExp("\\s*(?:\\(" + escapedTicker + "\\)|[—-]\\s*" + escapedTicker + ")\\s*$", "i"),
                ""
            ).trim() || ticker;
        }
        return companyName;
    }

    function initialsFor(label, ticker) {
        var ignored = /^(PLC|INC|INCORPORATED|CORP|CORPORATION|CO|COMPANY|LTD|LIMITED|GROUP|HOLDINGS|TRUST|CLASS|THE|ETF)$/i;
        var words = String(label || "").match(/[A-Za-z0-9]+/g) || [];
        words = words.filter(function (word) {
            return !ignored.test(word) && word.toUpperCase() !== String(ticker || "").toUpperCase();
        });
        if (words.length >= 2) {
            return (words[0][0] + words[1][0]).toUpperCase();
        }
        if (words.length) {
            return words[0].slice(0, 2).toUpperCase();
        }
        return String(ticker || "?").replace(/[^A-Z0-9]/gi, "").slice(0, 2).toUpperCase() || "?";
    }

    function createIdentity(ticker, label, size) {
        var safeTicker = String(ticker || "").trim().toUpperCase();
        var safeSize = ["detail", "card", "compact"].indexOf(size) >= 0 ? size : "card";
        var identity = document.createElement("span");
        identity.className = "company-identity company-identity--" + safeSize;
        identity.dataset.companyIdentity = safeTicker;

        var frame = document.createElement("span");
        frame.className = "company-logo-frame";
        var fallback = document.createElement("span");
        fallback.className = "company-logo-fallback";
        fallback.setAttribute("aria-hidden", "true");
        fallback.textContent = initialsFor(label, safeTicker);

        if (safeTicker && !fallbackTickers[safeTicker]) {
            var providerSymbol = providerSymbolOverrides[safeTicker] || safeTicker;
            var image = document.createElement("img");
            image.className = "company-logo-image";
            image.src = "https://financialmodelingprep.com/image-stock/" + encodeURIComponent(providerSymbol) + ".png";
            image.alt = companyNameForAlt(label, safeTicker) + " logo";
            image.loading = safeSize === "detail" ? "eager" : "lazy";
            image.decoding = "async";
            image.referrerPolicy = "no-referrer";
            frame.appendChild(image);
        }
        frame.appendChild(fallback);
        identity.appendChild(frame);

        var name = document.createElement("span");
        name.className = "company-identity-name";
        name.textContent = String(label || safeTicker);
        identity.appendChild(name);
        return identity;
    }

    document.addEventListener("error", function (event) {
        var image = event.target;
        if (image && image.classList && image.classList.contains("company-logo-image")) {
            handleLogoError(image);
        }
    }, true);

    window.StockRadarCompanyLogos = {
        createIdentity: createIdentity,
        handleError: handleLogoError,
        showInitials: showInitials
    };
}());
