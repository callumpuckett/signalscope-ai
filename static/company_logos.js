(function () {
    "use strict";

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

        if (safeTicker) {
            var providerSymbol = safeTicker === "V" ? "0QZ0.L" : safeTicker;
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
