"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { websiteCopy, type ContentCardData, type Language, type SnapshotStatData } from "./copy";

interface SiteHeaderProps {
  copy: (typeof websiteCopy)[Language];
  language: Language;
  onToggleLanguage: () => void;
}

interface ContentCardProps {
  card: ContentCardData;
}

interface StatCardProps {
  stat: SnapshotStatData;
}

function LanguageToggle({ language, onToggle }: { language: Language; onToggle: () => void }) {
  return (
    <button className="language-toggle" type="button" onClick={onToggle} aria-label="Toggle website language">
      {language === "en" ? "ES" : "EN"}
    </button>
  );
}

function SiteHeader({ copy, language, onToggleLanguage }: SiteHeaderProps) {
  return (
    <header className="site-header">
      <div className="site-header-inner">
        <Link className="brand" href="/" aria-label="TrackFlow home">
          <img src="https://images.unsplash.com/photo-1586528116311-ad8dd3c8310d?auto=format&fit=crop&w=120&q=80" alt="" />
          <span><strong>TrackFlow</strong><small>{copy.tagline}</small></span>
        </Link>
        <nav className="desktop-nav" aria-label="Primary">
          <a href="#features">{copy.navFeatures}</a>
          <a href="#how-it-works">{copy.navHow}</a>
          <a href="#why-trackflow">{copy.navWhy}</a>
          <Link className="button button-primary nav-apply" href="/application">{copy.navApply}</Link>
          <LanguageToggle language={language} onToggle={onToggleLanguage} />
        </nav>
        <div className="mobile-actions">
          <LanguageToggle language={language} onToggle={onToggleLanguage} />
          <Link className="button button-primary" href="/application">{copy.mobileApply}</Link>
        </div>
      </div>
    </header>
  );
}

function ContentCard({ card }: ContentCardProps) {
  return (
    <article className={`content-card tone-${card.tone}`}>
      {card.eyebrow && <p className="card-eyebrow">{card.eyebrow}</p>}
      <h3>{card.title}</h3>
      <p>{card.description}</p>
    </article>
  );
}

function StatCard({ stat }: StatCardProps) {
  return <article className="stat-card"><p>{stat.label}</p><strong>{stat.value}</strong></article>;
}

function SiteFooter({ copy }: { copy: (typeof websiteCopy)[Language] }) {
  return (
    <footer className="site-footer">
      <div className="footer-inner">
        <section aria-labelledby="contact-title">
          <h2 id="contact-title">{copy.contactTitle}</h2>
          <p>{copy.contactEmail}</p>
          <p>{copy.contactLocation}</p>
        </section>
        <section className="footer-action" aria-label="Action links">
          <Link className="button button-primary" href="/application">{copy.footerApply}</Link>
          <p>{copy.footerTagline}</p>
        </section>
      </div>
      <p className="footer-rights">{copy.footerRights}</p>
    </footer>
  );
}

export function WebsiteHome() {
  const [language, setLanguage] = useState<Language>("en");
  const copy = websiteCopy[language];

  useEffect(() => {
    const savedLanguage = window.localStorage.getItem("trackflow-lang");
    if (savedLanguage === "es") {
      document.documentElement.lang = "es";
      setLanguage("es");
    }
  }, []);

  function toggleLanguage() {
    const nextLanguage = language === "en" ? "es" : "en";
    window.localStorage.setItem("trackflow-lang", nextLanguage);
    document.documentElement.lang = nextLanguage;
    setLanguage(nextLanguage);
  }

  return (
    <>
      <a className="skip-link" href="#main-content">Skip to content</a>
      <SiteHeader copy={copy} language={language} onToggleLanguage={toggleLanguage} />
      <main id="main-content">
        <section className="hero" aria-labelledby="hero-title">
          <div className="hero-inner">
            <article className="hero-copy">
              <p className="hero-badge">{copy.heroBadge}</p>
              <h1 id="hero-title">{copy.heroTitle}</h1>
              <p className="hero-description">{copy.heroText}</p>
              <div className="hero-actions">
                <Link className="button button-primary button-large" href="/application">{copy.heroStart}</Link>
                <a className="button button-secondary button-large" href="#how-it-works">{copy.heroHow}</a>
              </div>
            </article>
            <article className="hero-visual">
              <img src="https://images.unsplash.com/photo-1542838132-92c53300491e?auto=format&fit=crop&w=1200&q=80" alt="Warehouse staff scanning boxes for outbound shipments" />
              <div className="hero-stats">
                <div><strong>130</strong><span>{copy.teamMembers}</span></div>
                <div><strong>2</strong><span>{copy.warehouseSites}</span></div>
                <div><strong>8</strong><span>{copy.carrierPartners}</span></div>
              </div>
            </article>
          </div>
        </section>

        <section className="content-section" id="features" aria-labelledby="features-title">
          <div className="section-inner">
            <h2 id="features-title">{copy.featuresTitle}</h2>
            <p className="section-intro">{copy.featuresText}</p>
            <div className="feature-grid">{copy.features.map((card) => <ContentCard key={card.title} card={card} />)}</div>
          </div>
        </section>

        <section className="process-section" id="how-it-works" aria-labelledby="how-title">
          <div className="section-inner">
            <h2 id="how-title">{copy.howTitle}</h2>
            <div className="process-grid">{copy.steps.map((card) => <ContentCard key={card.title} card={card} />)}</div>
          </div>
        </section>

        <section className="content-section" id="why-trackflow" aria-labelledby="why-title">
          <div className="section-inner">
            <h2 id="why-title">{copy.whyTitle}</h2>
            <div className="reason-grid">{copy.reasons.map((card) => <ContentCard key={card.title} card={card} />)}</div>
          </div>
        </section>

        <section className="snapshot-section" aria-labelledby="snapshot-title">
          <div className="section-inner">
            <h2 id="snapshot-title">{copy.snapshotTitle}</h2>
            <p className="section-intro">{copy.snapshotText}</p>
            <div className="snapshot-stats">{copy.stats.map((stat) => <StatCard key={stat.label} stat={stat} />)}</div>
            <div className="snapshot-grid">{copy.snapshots.map((card) => <ContentCard key={card.title} card={card} />)}</div>
          </div>
        </section>
      </main>
      <SiteFooter copy={copy} />
    </>
  );
}